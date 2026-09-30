"""Unit + penetration-style tests of the security invariants."""
import asyncio
import json
import logging
import os
import re
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

import tsabot  # noqa: F401
from tsabot.api import create_app
from tsabot.config import Settings
from tsabot.exchange.binance import (ALLOWED, BinanceFutures, ExchangeError, ForbiddenEndpoint, SymbolRules,
                                     check_endpoint, sign)
from tsabot.risk import Risk
from tsabot.secrets import RedactFilter, Secret, Vault

KEY = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789KEYKEYKEY"
SECRET = "ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210SECRETSECRET"
PW = "correct horse battery"


# ------------------------------------------------------------------ exchange allow-list
@pytest.mark.parametrize("method,path", [
    ("POST", "/sapi/v1/capital/withdraw/apply"), ("POST", "/sapi/v1/asset/transfer"),
    ("POST", "/sapi/v1/futures/transfer"), ("POST", "/fapi/v1/asset/wallet/transfer"),
    ("POST", "/sapi/v1/sub-account/universalTransfer"), ("GET", "/sapi/v1/capital/deposit/address"),
    ("POST", "/sapi/v1/convert/acceptQuote"), ("DELETE", "/fapi/v1/order"), ("GET", "/fapi/v1/../../sapi/v1/x"),
    ("POST", "/fapi/v1/order/../../sapi/v1/capital/withdraw/apply")])
def test_forbidden_endpoints(method, path):
    with pytest.raises(ForbiddenEndpoint):
        check_endpoint(method, path)


# read-only account-mode query ("Multi-Assets" = margin mode, not a wallet asset); changing it is NOT allowed
READ_ONLY_EXCEPTIONS = {("GET", "/fapi/v1/multiAssetsMargin")}


def test_allowlist_has_no_wallet_endpoints():
    for m, p in ALLOWED - READ_ONLY_EXCEPTIONS:
        assert not re.search(r"withdraw|transfer|deposit|capital|convert|sub-?account|loan|asset", p, re.I), p
    assert ("POST", "/fapi/v1/multiAssetsMargin") not in ALLOWED
    assert [p for m, p in ALLOWED if p.startswith("/sapi")] == ["/sapi/v1/account/apiRestrictions"]
    assert all(m == "GET" for m, p in ALLOWED if p.startswith("/sapi"))


def test_source_has_no_wallet_calls():
    src = "".join(p.read_text(encoding="utf-8") for p in (tsabot.BOT_ROOT / "tsabot").rglob("*.py"))
    for bad in ("/withdraw", "capital/", "asset/transfer", "futures/transfer", "universalTransfer"):
        assert bad not in src
    assert "follow_redirects=False" in src


def test_signature_matches_binance_doc_example():
    q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert sign("NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j", q) == \
        "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_signed_request_never_puts_secret_on_the_wire():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(400, json={"code": -1, "msg": f"echo {req.url} {dict(req.headers)}"})
    c = BinanceFutures("testnet", Secret(KEY), Secret(SECRET), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ExchangeError) as ei:
        asyncio.run(c.account())
    for r in seen:                                   # clock sync (public) + the signed call
        assert r.url.host == "demo-fapi.binance.com"
        assert SECRET not in str(r.url) and SECRET not in str(r.headers) and SECRET.encode() not in r.content
    r = [x for x in seen if x.url.path == "/fapi/v3/account"][-1]
    assert r.headers["X-MBX-APIKEY"] == KEY and "signature=" in str(r.url)
    assert KEY not in str(ei.value) and "signature=" not in str(ei.value).replace("signature=***", "")


def test_foreign_host_refused():
    c = BinanceFutures("live", Secret(KEY), Secret(SECRET))
    with pytest.raises(ForbiddenEndpoint):
        asyncio.run(c.request("GET", "/fapi/v1/time", base="https://evil.example.com"))


# ------------------------------------------------------------------ secrets
def test_secret_repr_hidden():
    s = Secret(SECRET)
    assert SECRET not in repr(s) and SECRET not in str(s) and SECRET not in f"{s}" and s.reveal() == SECRET


def test_vault_roundtrip_and_encryption(tmp_path):
    v = Vault(tmp_path / "vault.json")
    v.save(PW, KEY, SECRET)
    raw = (tmp_path / "vault.json").read_text()
    assert KEY not in raw and SECRET not in raw
    if os.name != "nt":                      # Windows: the file lives in the user's own profile folder
        assert stat.S_IMODE(os.stat(tmp_path / "vault.json").st_mode) == 0o600
    k, s = v.load(PW)
    assert k.reveal() == KEY and s.reveal() == SECRET
    assert v.load("wrong password!!") is None


def test_log_redaction(caplog):
    f = RedactFilter()
    f.add(Secret(SECRET), KEY)
    lg = logging.getLogger("tsabot.test")
    lg.addFilter(f)
    with caplog.at_level(logging.INFO, logger="tsabot.test"):
        lg.info("req %s", f"https://fapi.binance.com/x?a=1&signature=deadbeef01 key={KEY} s={SECRET}")
        lg.info({"api_secret": "plainvalue123"})
    text = caplog.text
    assert SECRET not in text and KEY not in text and "deadbeef01" not in text and "plainvalue123" not in text


# ------------------------------------------------------------------ settings / risk
@pytest.mark.parametrize("sym", ["BTCBUSD", "ETHBTC", "BTCUSDC", "../etc", "<script>", "BTC USDT", "btcusdt!", ""])
def test_only_usdt_symbols(sym):
    assert Settings(symbols=[sym]).validate()


def test_settings_limits():
    assert not Settings().validate()
    assert Settings(leverage=50).validate() and Settings(budget_usdt=-1).validate()
    assert Settings(max_positions=0).validate() and Settings(mode="yolo").validate()
    assert Settings(allow_long=False, allow_short=False).validate()
    assert Settings(leverage=10, emergency_stop_pct=9.5).validate()   # liquidation would come first
    assert not Settings(leverage=10, emergency_stop_pct=8.0).validate()


def test_risk_budget_and_limits():
    s = Settings(budget_usdt=100, max_positions=4, leverage=2, daily_loss_limit_pct=5, max_drawdown_pct=20)
    r = Risk(s)
    rules = SymbolRules("XUSDT", *map(__import__("decimal").Decimal, ("0.001", "0.001", "1000")),
                        __import__("decimal").Decimal("0.01"), 5.0)
    qty, margin = r.size(10.0, rules, 0.0)
    assert qty == 4.975 and margin == pytest.approx(24.875)   # 0.5% head-room for a worse fill
    assert r.can_open([25, 25, 25], 0, 0, None)[0]
    assert not r.can_open([25, 25, 25, 25], 0, 0, None)[0]
    assert not r.can_open([], 0, -5.0, None)[0]            # daily loss limit
    assert not r.can_open([], -20.0, 0, None)[0]           # max drawdown
    assert r.stop_reason(-20.0)
    assert r.effective_budget(-10) == 90 and r.effective_budget(+10) == 100
    s.compound = True
    assert r.effective_budget(+10) == 110
    assert not r.can_open([], 0, 0, available=10.0)[0]     # exchange balance lower than slot
    assert r.size(1_000_000.0, rules, 0.0)[0] == 0.0       # below min qty -> skip


# ------------------------------------------------------------------ web API (pen tests)
class FakeClient:
    def __init__(self, restrictions):
        self.r = restrictions

    async def sync_time(self):
        return 0

    async def account(self):
        return {"availableBalance": "123.4"}

    async def dual_side(self):
        return False

    async def api_restrictions(self):
        return self.r

    async def close(self):
        pass


def make(tmp_path, restrictions=None, model=True):
    mp = tmp_path / "model.json"
    if model:
        mp.write_text(json.dumps({"long": {"rules": [[["rsi", "<=", -0.9]]], "H": 6}, "short": {"rules": [], "H": 6}}))
    fac = {"client": lambda venue, keys: FakeClient(restrictions or {
        "enableFutures": True, "enableWithdrawals": False, "enableInternalTransfer": False,
        "permitsUniversalTransfer": False})}
    app = create_app(tmp_path / "data", mp, fac)
    return app, TestClient(app, base_url="http://127.0.0.1")


def login(app, c):
    st = app.state.tsa
    r = c.post("/api/auth/setup", json={"token": st.setup_token, "password": PW})
    assert r.status_code == 200
    return r.headers["x-csrf-token"]


def test_host_header_and_origin(tmp_path):
    app, c = make(tmp_path)
    assert c.get("/", headers={"host": "evil.com"}).status_code == 400
    assert c.get("/api/auth/state", headers={"host": "attacker.localhost.evil"}).status_code == 400
    r = c.post("/api/auth/login", json={"password": "x"}, headers={"origin": "http://evil.com"})
    assert r.status_code == 403


def test_everything_requires_auth(tmp_path):
    app, c = make(tmp_path)
    for route in app.routes:
        p = getattr(route, "path", "")
        if not p.startswith("/api/") or p.startswith("/api/auth/state") or p in ("/api/auth/setup", "/api/auth/login"):
            continue
        for m in route.methods:
            r = c.request(m, p, json={}) if m != "GET" else c.get(p)
            assert r.status_code in (401, 403), (m, p, r.status_code)


def test_setup_token_and_bruteforce_lockout(tmp_path):
    app, c = make(tmp_path)
    assert c.post("/api/auth/setup", json={"token": "wrong", "password": PW}).status_code == 403
    login(app, c)
    assert c.post("/api/auth/setup", json={"token": "x", "password": PW}).status_code == 409
    c.cookies.clear()
    codes = [c.post("/api/auth/login", json={"password": f"bad{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 429   # locked even with the right one


def test_csrf_and_content_type(tmp_path):
    app, c = make(tmp_path)
    tok = login(app, c)
    assert c.post("/api/bot/stop", json={}).status_code == 403                        # no CSRF header
    assert c.post("/api/bot/stop", json={}, headers={"x-csrf-token": "nope"}).status_code == 403
    r = c.post("/api/bot/stop", content="a=1", headers={"x-csrf-token": tok, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    assert c.post("/api/bot/stop", json={}, headers={"x-csrf-token": tok}).status_code == 200
    big = {"password": "x" * 20000}
    assert c.post("/api/auth/login", json=big).status_code == 413


def test_security_headers_and_static_traversal(tmp_path):
    app, c = make(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    h = r.headers
    assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert "server" not in {k.lower() for k in h} or "uvicorn" not in h.get("server", "")
    for p in ("/static/../tsabot/api.py", "/static/%2e%2e/tsabot/api.py", "/static/..%2Fdata%2Fvault.json"):
        assert c.get(p).status_code in (400, 404)
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_api_secret_never_leaves(tmp_path):
    app, c = make(tmp_path)
    tok = login(app, c)
    H = {"x-csrf-token": tok}
    assert c.put("/api/keys", json={"password": "wrong-password", "api_key": KEY, "api_secret": SECRET}, headers=H).status_code == 401
    assert c.put("/api/keys", json={"password": PW, "api_key": KEY, "api_secret": SECRET}, headers=H).status_code == 200
    bodies = []
    for route in app.routes:
        p = getattr(route, "path", "")
        if p.startswith("/api/") and "GET" in getattr(route, "methods", ()):
            bodies.append(c.get(p).text)
    bodies.append(c.post("/api/keys/check", json={"venue": "live"}, headers=H).text)
    blob = "\n".join(bodies)
    assert SECRET not in blob and KEY not in blob
    for f in (tmp_path / "data").iterdir():
        assert SECRET.encode() not in f.read_bytes() and KEY.encode() not in f.read_bytes(), f
    # key is usable after re-login (vault unlocked with the password)
    c.post("/api/auth/logout", json={}, headers=H)
    app.state.tsa.keys = None
    c.post("/api/auth/login", json={"password": PW})
    assert app.state.tsa.keys[1].reveal() == SECRET


def test_live_refused_without_confirmation_or_with_withdraw_key(tmp_path):
    app, c = make(tmp_path, restrictions={"enableFutures": True, "enableWithdrawals": True,
                                          "enableInternalTransfer": False, "permitsUniversalTransfer": False})
    tok = login(app, c)
    H = {"x-csrf-token": tok}
    c.put("/api/keys", json={"password": PW, "api_key": KEY, "api_secret": SECRET}, headers=H)
    assert c.put("/api/settings", json={"mode": "live"}, headers=H).status_code == 200
    r = c.post("/api/bot/start", json={}, headers=H)
    assert r.status_code == 400 and "onay" in r.json()["detail"].lower()
    c.put("/api/settings", json={"mode": "live", "live_confirmed": True}, headers=H)
    r = c.post("/api/bot/start", json={"password": PW}, headers=H)
    assert r.status_code == 400 and "çekme" in r.json()["detail"]
    assert app.state.tsa.engine is None


def test_settings_validation_via_api(tmp_path):
    app, c = make(tmp_path)
    H = {"x-csrf-token": login(app, c)}
    assert c.put("/api/settings", json={"symbols": ["BTCUSDT", "<img src=x>"]}, headers=H).status_code == 422
    assert c.put("/api/settings", json={"leverage": 125}, headers=H).status_code == 422
    assert c.put("/api/settings", json={"budget_usdt": 250, "symbols": ["btcusdt", "ETHUSDT"]}, headers=H).json()["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert c.put("/api/settings", json=[1, 2], headers=H).status_code == 400


def test_no_wallet_routes(tmp_path):
    app, _ = make(tmp_path)
    for r in app.routes:
        assert not re.search(r"withdraw|transfer|deposit", getattr(r, "path", ""), re.I)
