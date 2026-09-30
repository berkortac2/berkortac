"""Controller / engine / API features of v1.1: manual closes, stop modes, settings while running, budget
period, settings persistence, vault v2 + remember-me, wallet, history ranges, NaN-safe JSON."""
import asyncio
import json
import time
from dataclasses import asdict

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import tsabot  # noqa: F401
from tsabot.api import create_app
from tsabot.broker import Fill, PaperBroker
from tsabot.config import Settings, load_settings, save_settings
from tsabot.control import ControlError
from tsabot.engine import Engine, Position, day_start_ms
from tsabot.market import BAR_MS
from tsabot.secrets import Vault, write_private
from tsabot.store import Store
from tsabot.strategy import DirModel, Signal

from test_robustness import RULES  # noqa: E402
from test_security import KEY, PW, SECRET  # noqa: E402

T0 = 1_700_000_100_000 - (1_700_000_100_000 % BAR_MS)


class Mkt:
    realtime = False

    def __init__(self, t=T0):
        self.t = t

    def now_ms(self):
        return self.t + BAR_MS

    async def rules(self):
        return {s: RULES for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT")}

    async def wait_next_close(self, stop):
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            pass
        self.t += BAR_MS

    async def update(self, sym):
        rows = [{"open_time": self.t, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0,
                 "close_time": self.t + BAR_MS - 1}]
        return pd.DataFrame(rows), pd.DataFrame(rows)

    async def price(self, sym):
        return 10.0

    async def prices(self, syms):
        return {s: 10.0 for s in syms}


class Strat:
    def __init__(self, d=1, H=6):
        self.d, self.H = d, H

    def evaluate(self, sym, k5, k1, al, ash):
        return Signal(sym, int(k5["open_time"].iloc[-1]), self.d, 0, 10.0, 0.5, {})

    def params(self, d):
        return DirModel(rules=[], H=self.H, tp_atr=None, sl_atr=2.0)


class LevBroker(PaperBroker):
    def __init__(self):
        super().__init__()
        self.lev = []

    def set_leverage(self, lev):
        self.lev.append(lev)


def engine(settings=None, strat=None, broker=None, store=None, syms=("AAAUSDT",)):
    s = settings or Settings(symbols=list(syms), mode="paper", budget_usdt=100, max_positions=4)
    e = Engine(s, strat or Strat(), Mkt(), broker or PaperBroker(), store or Store(":memory:"))
    e.rules = {x: RULES for x in syms}
    e.symbols = list(syms)
    return e


def pos(sym="AAAUSDT", qty=1.0, margin=10.0):
    return Position(sym, 1, qty, 10.0, T0, T0, 96, None, 9.0, 9.2, margin, 0.005, 0)


# ---------------------------------------------------------------- manual close
def test_close_one_position_leaves_the_others():
    e = engine(syms=("AAAUSDT", "BBBUSDT"))
    e.positions = {"AAAUSDT": pos("AAAUSDT"), "BBBUSDT": pos("BBBUSDT")}
    r = asyncio.run(e.close_positions(["AAAUSDT"]))
    assert r == {"AAAUSDT": "ok"} and list(e.positions) == ["BBBUSDT"]
    t = e.store.trades("paper")
    assert len(t) == 1 and t[0]["reason"] == "Elle kapatıldı" and t[0]["symbol"] == "AAAUSDT"
    assert asyncio.run(e.close_positions(["ZZZUSDT"]))["ZZZUSDT"].startswith("Botun")


def test_close_all_reports_failures_and_keeps_closing():
    class Bad(PaperBroker):
        async def close(self, symbol, direction, qty, ref_price, slip=True):
            if symbol == "AAAUSDT":
                raise RuntimeError("rejected")
            return await super().close(symbol, direction, qty, ref_price, slip)
    e = engine(broker=Bad(), syms=("AAAUSDT", "BBBUSDT"))
    e.positions = {"AAAUSDT": pos("AAAUSDT"), "BBBUSDT": pos("BBBUSDT")}
    r = asyncio.run(e.close_positions())
    assert r["BBBUSDT"] == "ok" and "rejected" in r["AAAUSDT"] and list(e.positions) == ["AAAUSDT"]


# ---------------------------------------------------------------- stop modes
def test_drain_stop_manages_open_positions_then_stops():
    e = engine(strat=Strat(1, H=3), syms=("AAAUSDT", "BBBUSDT"))

    async def go():
        await e.start()
        for _ in range(100):
            if e.positions:
                break
            await asyncio.sleep(0.02)
        n_before = len(e.store.trades("paper", 1000))
        state = await e.pause()
        assert state == "draining" and e.state == "draining"
        opened = set(e.positions)
        for _ in range(400):                  # time exit (H=3) closes them, then the loop ends by itself
            if not e.running:
                break
            await asyncio.sleep(0.02)
        return opened, n_before
    opened, n_before = asyncio.run(go())
    assert opened and not e.positions and not e.running and e.state == "stopped"
    closed = e.store.trades("paper", 1000)
    assert len(closed) == n_before + len(opened)          # nothing new was opened while draining
    assert any("Açık pozisyon kalmadı" in x["msg"] for x in e.store.events())


def test_pause_without_positions_stops_at_once_and_start_resumes():
    e = engine(strat=Strat(0))

    async def go():
        await e.start()
        await asyncio.sleep(0.05)
        st = await e.pause()
        return st
    assert asyncio.run(go()) == "stopped" and not e.running


def test_no_entries_while_draining():
    e = engine(strat=Strat(1), syms=("AAAUSDT", "BBBUSDT"))
    e.positions = {"AAAUSDT": pos("AAAUSDT")}
    e.entries = False
    asyncio.run(e.on_bar())
    assert "BBBUSDT" not in e.positions


# ---------------------------------------------------------------- settings while running
def test_leverage_and_stop_apply_to_the_next_position():
    b = LevBroker()
    e = engine(broker=b, syms=("AAAUSDT", "BBBUSDT"))
    e.positions = {"AAAUSDT": pos("AAAUSDT")}
    old_stop = e.positions["AAAUSDT"].emergency
    e.apply_settings(Settings(symbols=["AAAUSDT", "BBBUSDT"], mode="paper", budget_usdt=100, max_positions=4,
                              leverage=3, emergency_stop_pct=5.0))
    assert b.lev == [3] and e.risk.s.leverage == 3
    asyncio.run(e.on_bar())
    p = e.positions["BBBUSDT"]
    assert p.emergency == pytest.approx(p.entry_price * 0.95) and p.margin == pytest.approx(p.qty * p.entry_price / 3)
    assert e.positions["AAAUSDT"].emergency == old_stop                 # open position keeps its stop


def test_budget_period_starts_when_the_budget_changes():
    st = Store(":memory:")
    for i, pnl in enumerate((-30.0, +50.0)):
        st.add_trade({"mode": "paper", "symbol": "AAAUSDT", "direction": 1, "qty": 1, "entry_time": T0 + i,
                      "entry_price": 10, "exit_time": T0 + i * 1000, "exit_price": 10, "reason": "x", "fees": 0,
                      "pnl": pnl, "margin": 10, "rule": 0})
    e = engine(store=st, settings=Settings(symbols=["AAAUSDT"], mode="paper", budget_usdt=100, budget_set_at=0))
    period, _ = e.realized()
    assert period == pytest.approx(20.0) and e.risk.effective_budget(period) == 100   # profit never raises it
    e.apply_settings(Settings(symbols=["AAAUSDT"], mode="paper", budget_usdt=100, budget_set_at=T0 + 500))
    period, _ = e.realized()
    assert period == pytest.approx(50.0)
    e.apply_settings(Settings(symbols=["AAAUSDT"], mode="paper", budget_usdt=100, budget_set_at=T0 + 2000))
    assert e.realized()[0] == 0.0


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time zone switching needs time.tzset (POSIX)")
def test_local_day_start(monkeypatch):
    import datetime as dt
    monkeypatch.setenv("TZ", "Europe/Istanbul")          # UTC+3: the local day starts at 21:00 UTC
    time.tzset()
    try:
        now = int(dt.datetime(2026, 9, 30, 1, 30, tzinfo=dt.timezone(dt.timedelta(hours=3))).timestamp() * 1000)
        d = day_start_ms(now)
        assert dt.datetime.fromtimestamp(d / 1000, dt.timezone.utc) == dt.datetime(2026, 9, 29, 21, 0, tzinfo=dt.timezone.utc)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


def test_manual_partial_close_on_the_exchange_is_followed():
    class B(PaperBroker):
        simulated = False

        async def closed_by_exchange(self, o):
            return {}

        async def sync_quantities(self, o):
            return {"AAAUSDT": 0.4}

        async def ensure_stop(self, *a, **kw):
            self.qty = kw.get("qty")
            return True
    b = B()
    e = engine(broker=b, strat=Strat(0))
    e.positions = {"AAAUSDT": pos(qty=1.0, margin=10.0)}
    asyncio.run(e.on_bar())
    p = e.positions["AAAUSDT"]
    assert p.qty == 0.4 and p.margin == pytest.approx(4.0) and b.qty == 0.4 and p.protected


def test_exchange_close_reason_is_derived_from_the_price():
    class B(PaperBroker):
        simulated = False

        async def closed_by_exchange(self, o):
            return {"AAAUSDT": Fill(9.2, 1.0, 0.0)}
    e = engine(broker=B(), strat=Strat(0))
    e.positions = {"AAAUSDT": pos()}
    asyncio.run(e.on_bar())
    assert e.store.trades("paper")[0]["reason"] == "Zarar kes (borsadaki stop)"


def test_notifications_reach_listeners_and_never_break_trading():
    got = []
    e = engine(strat=Strat(1))
    e.listeners += [lambda k, d: got.append((k, d)), lambda k, d: 1 / 0]
    asyncio.run(e.on_bar())
    asyncio.run(e.close_positions())
    kinds = [k for k, _ in got]
    assert "trade_open" in kinds and "trade_close" in kinds
    close = [d for k, d in got if k == "trade_close"][0]
    assert close["symbol"] == "AAAUSDT" and "pnl" in close and close["mode"] == "paper"


# ---------------------------------------------------------------- persistence
def test_settings_survive_a_restart(tmp_path):
    p = tmp_path / "settings.json"
    save_settings(Settings(leverage=3, emergency_stop_pct=6.5, budget_usdt=250), p)
    s = load_settings(p)
    assert (s.leverage, s.emergency_stop_pct, s.budget_usdt) == (3, 6.5, 250)
    d = json.loads(p.read_text())
    d["max_positions"] = 99                       # e.g. a limit changed in a newer version
    write_private(p, json.dumps(d))
    s = load_settings(p)
    assert s.max_positions == Settings().max_positions and s.leverage == 3 and s.emergency_stop_pct == 6.5


# ---------------------------------------------------------------- vault v2 / remember me
class FakeProtector:
    available = True

    def protect(self, b):
        return b"P" + b[::-1]

    def unprotect(self, b):
        if not b.startswith(b"P"):
            raise OSError("bad")
        return b[1:][::-1]


def test_vault_v2_items_and_v1_migration(tmp_path):
    v = Vault(tmp_path / "v.json")
    dek = v.create(PW)
    v.put(dek, "telegram", {"token": "123456789:" + "A" * 35})
    assert v.unlock("wrong password!") is None and v.unlock(PW) == dek
    assert v.get(dek, "telegram")["token"].endswith("A" * 35) and not v.has("binance")
    raw = (tmp_path / "v.json").read_text()
    assert "AAAAAAAAAA" not in raw
    # a v1 file (key + secret encrypted directly) is converted on the first unlock
    import base64
    import secrets as s_
    from cryptography.fernet import Fernet
    from tsabot.secrets import _kdf
    salt = s_.token_bytes(16)
    tok = Fernet(base64.urlsafe_b64encode(_kdf(PW, salt))).encrypt(json.dumps({"k": KEY, "s": SECRET}).encode())
    write_private(tmp_path / "v1.json", json.dumps({"v": 1, "salt": base64.b64encode(salt).decode(), "token": tok.decode()}))
    v1 = Vault(tmp_path / "v1.json")
    assert v1.has("binance")
    k, s = v1.load(PW)
    assert k.reveal() == KEY and s.reveal() == SECRET and json.loads((tmp_path / "v1.json").read_text())["v"] == 2


def make(tmp_path, desktop=False, protector=None, factories=None):
    mp = tmp_path / "model.json"
    mp.write_text(json.dumps({"long": {"rules": [[["rsi", "<=", -0.9]]], "H": 6}, "short": {"rules": [], "H": 6},
                              "stats": {"rows": [{"group": "g", "dir_hit": float("nan"), "win_rate": 0.5}]}}))
    app = create_app(tmp_path / "data", mp, factories, protector=protector, desktop=desktop)
    return app, TestClient(app, base_url="http://127.0.0.1")


def setup(app, c):
    r = c.post("/api/auth/setup", json={"token": app.state.tsa.setup_token, "password": PW})
    assert r.status_code == 200
    return {"x-csrf-token": r.headers["x-csrf-token"]}


def test_remember_me_unlocks_the_desktop_app_after_a_restart(tmp_path):
    app, c = make(tmp_path, desktop=True, protector=FakeProtector())
    H = setup(app, c)
    assert c.put("/api/keys", json={"password": PW, "api_key": KEY, "api_secret": SECRET}, headers=H).status_code == 200
    assert c.post("/api/auth/remember", json={"enable": True}, headers=H).json() == {"remembered": True}
    assert KEY.encode() not in (tmp_path / "data" / "remember.bin").read_bytes()
    # restart: new process, the window brings its per-process token
    app2, c2 = make(tmp_path, desktop=True, protector=FakeProtector())
    st2 = app2.state.tsa
    assert st2.keys is None
    assert c2.post("/api/auth/desktop", json={"token": "wrong"}).status_code == 403
    r = c2.post("/api/auth/desktop", json={"token": st2.desktop_token})
    assert r.status_code == 200 and st2.keys[1].reveal() == SECRET
    assert c2.get("/api/status").status_code == 200
    # without "remember me" the desktop token alone is not enough
    H2 = {"x-csrf-token": r.headers["x-csrf-token"]}
    c2.post("/api/auth/remember", json={"enable": False}, headers=H2)
    app3, c3 = make(tmp_path, desktop=True, protector=FakeProtector())
    assert c3.post("/api/auth/desktop", json={"token": app3.state.tsa.desktop_token}).status_code == 401


def test_desktop_login_is_refused_in_browser_mode(tmp_path):
    app, c = make(tmp_path)
    setup(app, c)
    c.cookies.clear()
    assert c.post("/api/auth/desktop", json={"token": ""}).status_code == 403


# ---------------------------------------------------------------- API
def paper_engine_factory(store_holder):
    def f(s, strategy, ctl):
        e = Engine(s, Strat(0), Mkt(), PaperBroker(), ctl.store)
        store_holder.append(e)
        return e
    return f


def test_api_close_and_stop_modes(tmp_path):
    made = []
    app, c = make(tmp_path, factories={"engine": paper_engine_factory(made)})
    H = setup(app, c)
    st = app.state.tsa
    st.store.put("positions:paper", {s: asdict(pos(s)) for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT")})
    # bot stopped: a single position can still be closed
    r = c.post("/api/positions/close", json={"symbol": "AAAUSDT"}, headers=H)
    assert r.status_code == 200 and "AAAUSDT" not in st.engine.positions
    assert c.post("/api/positions/close", json={"symbol": "AAAUSDT"}, headers=H).status_code == 400
    assert c.post("/api/positions/close", json={"symbol": "../x"}, headers=H).status_code == 422
    r = c.post("/api/positions/close-all", json={}, headers=H).json()
    assert r["closed"] == 2 and r["ok"] and not st.engine.positions
    assert len(c.get("/api/trades?range=all").json()) == 3
    assert c.get("/api/trades?range=bogus").status_code == 422
    s = c.get("/api/trades/summary").json()
    assert set(s) == {"today", "7d", "30d", "all"} and s["all"]["trades"] == 3
    assert c.post("/api/bot/stop", json={"how": "nope"}, headers=H).status_code == 422
    assert c.post("/api/bot/stop", json={"how": "full"}, headers=H).json()["running"] is False


def test_api_settings_while_running(tmp_path):
    made = []
    app, c = make(tmp_path, factories={"engine": paper_engine_factory(made)})
    H = setup(app, c)
    st = app.state.tsa

    async def go():
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
                                     cookies=c.cookies) as a:
            assert (await a.post("/api/bot/start", json={}, headers=H)).status_code == 200
            r1 = await a.put("/api/settings", json={"leverage": 3, "emergency_stop_pct": 5, "budget_usdt": 300},
                             headers=H)
            r2 = await a.put("/api/settings", json={"mode": "testnet"}, headers=H)
            r3 = await a.put("/api/settings", json={"symbols": ["BTCUSDT"]}, headers=H)
            r4 = await a.put("/api/settings", json={"budget_set_at": 1}, headers=H)
            await a.post("/api/bot/stop", json={"how": "full"}, headers=H)
            return r1, r2, r3, r4
    r1, r2, r3, r4 = asyncio.run(go())
    assert r1.status_code == 200 and r1.json()["leverage"] == 3 and made[0].s.leverage == 3
    assert T0 < r1.json()["budget_set_at"] <= made[0]._now()      # a new budget period (engine clock)
    assert r2.status_code == 409 and r3.status_code == 409
    assert r4.json()["budget_set_at"] == r1.json()["budget_set_at"]  # clients cannot move the period
    assert load_settings(st.settings_path).leverage == 3            # saved: survives a restart


def test_api_wallet_paper_and_ranges(tmp_path):
    app, c = make(tmp_path)
    H = setup(app, c)
    c.put("/api/settings", json={"paper_wallet_usdt": 2500}, headers=H)
    w = c.get("/api/wallet").json()
    assert w["source"] == "paper" and w["wallet"] == 2500 and w["allocatable"] == 2500
    for rng in ("today", "7d", "30d", "all"):
        assert c.get(f"/api/equity?range={rng}").status_code == 200
    assert c.get("/api/pnl/daily?days=30").json() == []
    assert c.get("/api/equity?range=5y").status_code == 422


def test_live_wallet_marks_the_users_positions(tmp_path):
    class Cl:
        async def account(self):
            return {"availableBalance": "600", "totalWalletBalance": "1000", "assets": []}

        async def positions(self, symbol=None):
            return [{"symbol": "AAAUSDT", "positionAmt": "1", "entryPrice": "10", "isolatedMargin": "10"},
                    {"symbol": "ETHUSDT", "positionAmt": "-2", "entryPrice": "3000", "isolatedMargin": "300"}]

        async def close(self):
            pass
    app, c = make(tmp_path, factories={"client": lambda v, k: Cl()})
    H = setup(app, c)
    c.put("/api/keys", json={"password": PW, "api_key": KEY, "api_secret": SECRET}, headers=H)
    c.put("/api/settings", json={"mode": "testnet"}, headers=H)
    app.state.tsa.store.put("positions:testnet", {"AAAUSDT": asdict(pos("AAAUSDT"))})
    w = c.get("/api/wallet").json()
    own = {p["symbol"]: p["owner"] for p in w["positions"]}
    assert own == {"AAAUSDT": "bot", "ETHUSDT": "user"} and w["user_margin"] == 300 and w["allocatable"] == 610


def test_app_prefs(tmp_path):
    app, c = make(tmp_path)
    H = setup(app, c)
    assert c.put("/api/app/prefs", json={"autostart": True}, headers=H).status_code == 400   # browser mode
    r = c.put("/api/app/prefs", json={"resume_bot": False, "notify_desktop": False}, headers=H).json()
    assert r["resume_bot"] is False and r["notify_desktop"] is False
    info = c.get("/api/app").json()
    assert info["prefs"]["resume_bot"] is False and info["desktop"] is False and info["version"]


def test_json_never_contains_nan(tmp_path):
    app, c = make(tmp_path)
    setup(app, c)
    r = c.get("/api/model")
    assert r.status_code == 200 and "NaN" not in r.text and r.json()["stats"]["rows"][0]["dir_hit"] is None


def test_controller_refuses_replay_without_data(tmp_path, monkeypatch):
    app, c = make(tmp_path)
    st = app.state.tsa
    st.settings = Settings(mode="replay")
    monkeypatch.setattr(st, "replay_available", lambda: False)
    with pytest.raises(ControlError):
        asyncio.run(st.start_bot("ui"))
