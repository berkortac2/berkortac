"""Regression tests for the independent security review (R1-R10). Each test fails if the
original weakness comes back."""
import asyncio
import http.server
import json
import logging
import socket
import threading
import time

import httpx
from fastapi.testclient import TestClient

import tsabot  # noqa: F401
from tsabot.api import create_app
from tsabot.broker import Fill
from tsabot.config import Settings
from tsabot.engine import Engine
from tsabot.exchange import binance as bx
from tsabot.exchange.binance import BinanceFutures, SymbolRules
from tsabot.secrets import REDACT, RedactFilter, Secret, valid_key
from tsabot.store import Store
from tsabot.strategy import DirModel, Signal

from test_security import KEY, PW, SECRET, login, make  # noqa: E402


class DummyEngine:
    started = []

    def __init__(self):
        self.mode, self._run, self.stopped, self.positions = "live", False, False, {}

    @property
    def running(self):
        return self._run and not self.stopped

    async def start(self):
        await asyncio.sleep(0.05)
        self._run = True
        DummyEngine.started.append(self)

    async def stop(self):
        self.stopped = True


class SlowClient:
    async def sync_time(self):
        await asyncio.sleep(0.2)

    async def account(self):
        return {"availableBalance": "1000"}

    async def dual_side(self):
        return False

    async def api_restrictions(self):
        return {"enableFutures": True, "enableWithdrawals": False, "enableInternalTransfer": False,
                "permitsUniversalTransfer": False}

    async def close(self):
        pass


def test_R1_double_start_single_engine(tmp_path):
    DummyEngine.started.clear()
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps({"long": {"rules": [], "H": 6}, "short": {"rules": [], "H": 6}}))
    app = create_app(tmp_path / "d", mp, {"client": lambda v, k: SlowClient(), "engine": lambda s, st, a: DummyEngine()})
    tsa = app.state.tsa

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
            r = await c.post("/api/auth/setup", json={"token": tsa.setup_token, "password": PW})
            H = {"x-csrf-token": r.headers["x-csrf-token"]}
            await c.put("/api/keys", json={"password": PW, "api_key": KEY, "api_secret": SECRET}, headers=H)
            await c.put("/api/settings", json={"mode": "live", "live_confirmed": True}, headers=H)
            bad = await c.post("/api/bot/start", json={"password": "wrong-password"}, headers=H)
            rs = await asyncio.gather(*(c.post("/api/bot/start", json={"password": PW}, headers=H) for _ in range(3)))
            return bad.status_code, [x.status_code for x in rs]
    bad, codes = asyncio.run(go())
    assert bad == 401                              # live start needs the password again
    assert codes == [200, 200, 200] and len(DummyEngine.started) == 1 and tsa.engine is DummyEngine.started[0]


class FakeMarket:
    def __init__(self, price_delay=0.0):
        self.price_delay, self.calls = price_delay, 0

    def now_ms(self):
        return 1_700_000_000_000

    async def rules(self):
        D = __import__("decimal").Decimal
        return {"AAAUSDT": SymbolRules("AAAUSDT", D("0.001"), D("0.001"), D("1000"), D("0.01"), 5.0)}

    async def wait_next_close(self, stop):
        self.calls += 1
        if self.calls > 1:
            await stop.wait()

    async def update(self, sym):
        import pandas as pd
        row = {"open_time": self.now_ms(), "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0,
               "close_time": self.now_ms() + 299_999}
        return pd.DataFrame([row]), pd.DataFrame([row])

    async def price(self, sym):
        await asyncio.sleep(self.price_delay)
        return 10.0


class FakeStrategy:
    def evaluate(self, sym, k5, k1, al, ash):
        return Signal(sym, int(k5["open_time"].iloc[-1]), 1, 0, 10.0, 0.5, {})

    def params(self, d):
        return DirModel(rules=[], H=6, tp_atr=None, sl_atr=2.0)


class FakeBroker:
    simulated = False
    external = set()

    def __init__(self, fail_after_fill=False, exchange_has_it=True):
        self.opened, self.fail, self.has = [], fail_after_fill, exchange_has_it
        self.brackets = []

    async def prepare(self, s, lev, own=()):
        return []

    async def available_usdt(self):
        return None

    async def position_of(self, sym):
        return (sum(q for _, q in self.opened), 10.0) if self.has else (0.0, None)

    async def open(self, sym, d, qty, px, rules):
        self.opened.append((sym, qty))
        if self.fail:
            raise httpx.ReadTimeout("order accepted by exchange, response lost")
        return Fill(px, qty, 0.0)

    async def close(self, sym, d, qty, px, slip=True):
        return Fill(px, qty, 0.0)

    async def set_brackets(self, *a):
        self.brackets.append(a)

    async def cancel_brackets(self, sym):
        return None

    async def closed_by_exchange(self, o):
        return {}


def mk_engine(broker, market):
    return Engine(Settings(symbols=["AAAUSDT"], mode="paper"), FakeStrategy(), market, broker, Store(":memory:"))


def test_R2_panic_blocks_in_flight_entry():
    async def go():
        b, m = FakeBroker(), FakeMarket(price_delay=0.3)
        e = mk_engine(b, m)
        await e.start()
        await asyncio.sleep(0.1)
        await e.panic()
        return e, b
    e, b = asyncio.run(go())
    assert not e.running and not b.opened and not e.positions


def test_R3_lost_order_response_is_reconciled_not_repeated():
    async def go():
        b, m = FakeBroker(fail_after_fill=True), FakeMarket()
        e = mk_engine(b, m)
        e.rules = await m.rules()
        e.symbols = ["AAAUSDT"]
        await e.on_bar()
        await e.on_bar()
        return e, b
    e, b = asyncio.run(go())
    assert len(b.opened) == 1                      # no second market order
    assert "AAAUSDT" in e.positions and b.brackets  # filled position adopted and protected


def test_R4_panic_after_restart_closes_persisted_positions(tmp_path):
    app, c = make(tmp_path)
    H = {"x-csrf-token": login(app, c)}
    tsa = app.state.tsa
    pos = {"symbol": "NEARUSDT", "direction": 1, "qty": 1.0, "entry_price": 2.0, "entry_time_ms": 1, "signal_bar": 0,
           "H": 6, "tp": None, "sl": 1.9, "emergency": None, "margin": 1.0, "fee_in": 0.0, "rule": 0}
    tsa.store.put("positions:paper", {"NEARUSDT": pos})
    assert c.get("/api/status").json()["positions"][0]["symbol"] == "NEARUSDT"   # visible while stopped

    class M:
        async def price(self, s):
            return 2.1
    from tsabot.broker import PaperBroker
    tsa.factories["engine"] = lambda s, strat, a: Engine(s, strat, M(), PaperBroker(), a.store)
    r = c.post("/api/bot/panic", json={}, headers=H)
    assert r.status_code == 200 and r.json()["closed"] == 1
    assert not tsa.store.get("positions:paper") and tsa.store.stats("paper")["trades"] == 1


def test_R5_validation_error_does_not_echo_input(tmp_path):
    app, c = make(tmp_path)
    H = {"x-csrf-token": login(app, c)}
    r = c.put("/api/keys", json={"password": PW, "api_key": KEY, "api_secret": SECRET * 4}, headers=H)
    assert r.status_code == 422 and SECRET not in r.text and KEY not in r.text


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_R6_keys_validated_and_tracebacks_redacted(monkeypatch, caplog):
    assert not valid_key(KEY + "\n") and not valid_key(" " + KEY) and valid_key(KEY)
    monkeypatch.setenv("BINANCE_API_KEY", KEY + "\n")
    monkeypatch.setenv("BINANCE_API_SECRET", SECRET)
    from tsabot.secrets import env_keys
    k = env_keys()
    assert k is not None and k[0].reveal() == KEY       # stripped
    f = RedactFilter()
    f.add(Secret(SECRET))
    lg = logging.getLogger("tsabot.r6")
    lg.addFilter(f)
    with caplog.at_level(logging.ERROR, logger="tsabot.r6"):
        try:
            raise RuntimeError(f"boom {SECRET}")
        except RuntimeError:
            lg.exception("failed")
    assert SECRET not in caplog.text


def test_R7_non_ascii_tokens_are_rejected_cleanly(tmp_path):
    app, _ = make(tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    assert c.post("/api/auth/setup", json={"token": "é", "password": PW}).status_code == 403
    c.post("/api/auth/setup", json={"token": app.state.tsa.setup_token, "password": PW})
    r = c.post("/api/bot/stop", content=b"{}", headers={"content-type": "application/json",
                                                        "x-csrf-token": "é".encode("latin-1")})
    assert r.status_code == 403


def test_R8_settings_types_are_strict(tmp_path):
    app, c = make(tmp_path)
    H = {"x-csrf-token": login(app, c)}
    for bad in ({"compound": "false"}, {"live_confirmed": "no"}, {"max_positions": 2.5}, {"leverage": 1.5},
                {"allow_long": 1}):
        assert c.put("/api/settings", json=bad, headers=H).status_code == 422, bad
    assert c.put("/api/settings", json={"leverage": 3.0}, headers=H).json()["leverage"] == 3


def test_R9_origin_port_checked_and_live_confirmation_fresh(tmp_path):
    app, c = make(tmp_path)
    H = {"x-csrf-token": login(app, c)}
    r = c.post("/api/bot/stop", json={}, headers={**H, "origin": "http://localhost:9999"})
    assert r.status_code == 403
    r = c.post("/api/bot/stop", json={}, headers={**H, "sec-fetch-site": "same-site"})
    assert r.status_code == 403
    assert c.put("/api/settings", json={"live_confirmed": True}, headers=H).json()["live_confirmed"] is False
    assert c.put("/api/settings", json={"mode": "live"}, headers=H).json()["live_confirmed"] is False
    assert c.put("/api/settings", json={"mode": "live", "live_confirmed": True}, headers=H).json()["live_confirmed"]
    assert c.put("/api/settings", json={"budget_usdt": 500}, headers=H).json()["live_confirmed"] is False


def test_R10_chunked_body_rejected(tmp_path):
    import uvicorn
    app, _ = make(tmp_path)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    body = b'{"password":"' + b"A" * 200_000 + b'"}'

    def raw(headers):
        so = socket.create_connection(("127.0.0.1", port), timeout=5)
        chunk = b"%x\r\n" % len(body) + body + b"\r\n0\r\n\r\n"
        so.sendall(b"POST /api/auth/login HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                   + headers + b"\r\n" + chunk)
        so.settimeout(5)
        data = so.recv(4096)
        so.close()
        return data.split(b"\r\n")[0]
    a = raw(b"Transfer-Encoding: chunked\r\n")
    b = raw(b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n")
    srv.should_exit = True
    t.join(5)
    assert (b"411" in a or b"400" in a) and (b"411" in b or b"400" in b)
