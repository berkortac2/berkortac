"""Telegram remote: parsing, pairing security, commands, confirmations, notifications, token secrecy."""
import asyncio
import json
import logging
import time
from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

import tsabot  # noqa: F401
from tsabot import reports
from tsabot.api import create_app
from tsabot.broker import PaperBroker
from tsabot.config import TelegramPrefs
from tsabot.engine import Engine
from tsabot.secrets import REDACT, RedactFilter, Secret
from tsabot.store import Store
from tsabot.telegram import TelegramAPI, TelegramError, TelegramService, norm_symbol, parse, valid_token

from test_control import Mkt, Strat, pos  # noqa: E402
from test_security import PW  # noqa: E402

TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-ab"
CHAT = 4242


class FakeAPI:
    instances = []

    def __init__(self, token, http=None):
        assert valid_token(token.reveal())
        self.sent, self.updates, self.calls = [], [], []
        FakeAPI.instances.append(self)

    async def call(self, method, _http_timeout=20.0, **p):
        self.calls.append((method, p))
        if method == "getMe":
            return {"id": 1, "username": "tsa_test_bot"}
        if method == "getUpdates":
            if self.updates:
                u, self.updates = self.updates, []
                return u
            await asyncio.sleep(0.02)
            return []
        if method == "sendMessage":
            self.sent.append(p)
            return {"message_id": len(self.sent)}
        return True

    async def close(self):
        pass


def msg(text, chat=CHAT, kind="private", age=0.0, uid=[0]):
    uid[0] += 1
    return {"update_id": uid[0], "message": {"chat": {"id": chat, "type": kind}, "from": {"username": "berk"},
                                             "text": text, "date": int(time.time() - age)}}


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("text,cmd", [("/başlat", "baslat"), ("/BAŞLAT", "baslat"), ("/Geçmiş 7", "gecmis"),
                                      ("/durdur@tsa_test_bot", "durdur"), ("/HEPSİNİKAPAT", "hepsinikapat"),
                                      ("/rapor", "rapor"), ("merhaba", None)])
def test_parse(text, cmd):
    assert parse(text)[0] == cmd


def test_symbol_and_token_formats():
    assert norm_symbol("sol") == "SOLUSDT" and norm_symbol("BTCUSDT") == "BTCUSDT" and norm_symbol("pepe'") == "PEPEUSDT"
    assert valid_token(TOKEN) and not valid_token("abc") and not valid_token(TOKEN + "/../x")


# ---------------------------------------------------------------- API client safety
def test_only_telegram_host_and_allowed_methods():
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, json={"ok": True, "result": {"username": "x"}})
    api = TelegramAPI(Secret(TOKEN), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asyncio.run(api.call("getMe"))
    assert seen[0].host == "api.telegram.org" and seen[0].scheme == "https"
    for bad in ("sendDocument", "setWebhook", "getFile", "forwardMessage"):
        with pytest.raises(PermissionError):
            asyncio.run(api.call(bad))


def test_network_error_never_carries_the_token():
    def handler(req):
        raise httpx.ConnectError(f"cannot connect to {req.url}")
    api = TelegramAPI(Secret(TOKEN), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(TelegramError) as ei:
        asyncio.run(api.call("getMe"))
    assert TOKEN not in str(ei.value) and ei.value.__cause__ is None and ei.value.__suppress_context__
    assert TOKEN.split(":")[1] not in repr(ei.value)


def test_token_is_redacted_from_logs(caplog):
    f = RedactFilter()
    lg = logging.getLogger("tsabot.tgtest")
    lg.addFilter(f)
    with caplog.at_level(logging.INFO, logger="tsabot.tgtest"):
        lg.info("HTTP Request: POST https://api.telegram.org/bot%s/getUpdates", TOKEN)
        lg.info("token was %s", TOKEN)
    assert TOKEN.split(":")[1] not in caplog.text


# ---------------------------------------------------------------- service with a fake controller
class Ctl:
    def __init__(self):
        self.store = Store(":memory:")
        self.tg_prefs = TelegramPrefs()
        self.started, self.stopped, self.closed = [], 0, []
        self.snap = {"state": "running", "mode": "live", "status": "Çalışıyor", "budget": 500, "margin_used": 62.5,
                     "max_positions": 8, "realized_today": 12.4, "realized_period": 40.0, "external": ["ETHUSDT"],
                     "positions": [{"symbol": "SOLUSDT", "direction": 1, "qty": 0.5, "entry_price": 140.0,
                                    "last_price": 142.8, "unrealized": 1.4, "roe_pct": 2.2, "entry_time_ms": 0,
                                    "sl": 136.0, "emergency": 128.8}]}

    def snapshot(self):
        return self.snap

    def now_ms(self):
        return int(time.time() * 1000)

    def trade_summary(self, rng):
        return {"trades": 2, "wins": 1, "win_rate": 0.5, "net_pnl": 12.4, "fees": 0.3, "best": 14, "worst": -1.6}

    def trades_in(self, rng, limit=200):
        return [{"symbol": "NEARUSDT", "direction": 1, "entry_price": 4.8, "exit_price": 4.95, "pnl": 14.0,
                 "margin": 62.5, "reason": "ÇIK sinyali (kâr al)", "exit_time": self.now_ms(), "qty": 10}]

    async def remote_start(self, source):
        self.started.append(source)
        return "▶️ Bot çalışıyor"

    async def remote_stop(self):
        self.stopped += 1
        return "⏸ Yeni işlem açma durduruldu."

    async def close_positions(self, syms, reason=""):
        self.closed.append((syms, reason))
        return {s: "ok" for s in (syms or ["SOLUSDT"])}

    def on_telegram_identity(self, me):
        self.tg_prefs.bot_username = me["username"]

    def on_telegram_paired(self, chat_id, name):
        self.tg_prefs.chat_id, self.tg_prefs.chat_name = chat_id, name


def service(paired=True):
    FakeAPI.instances.clear()
    ctl = Ctl()
    if paired:
        ctl.tg_prefs.chat_id = CHAT
    svc = TelegramService(ctl, api_factory=FakeAPI)
    svc.api = FakeAPI(Secret(TOKEN))
    return ctl, svc


def run(svc, *messages):
    async def go():
        for m in messages:
            await svc._handle(m["message"])
    asyncio.run(go())
    return [s["text"] for s in svc.api.sent]


def test_unpaired_or_foreign_chats_get_no_answer():
    ctl, svc = service(paired=False)
    assert run(svc, msg("/rapor"), msg("/baslat")) == [] and not ctl.started
    ctl2, svc2 = service()
    assert run(svc2, msg("/hepsinikapat", chat=999), msg("/baslat", chat=999)) == [] and not ctl2.started


def test_pairing_needs_the_code_from_the_app():
    ctl, svc = service(paired=False)
    code = svc.begin_pairing()["code"]
    run(svc, msg("/eslestir 000000" if code != "000000" else "/eslestir 111111"))
    assert ctl.tg_prefs.chat_id is None
    run(svc, msg(f"/eslestir {code}", kind="group"))                 # groups can never pair
    assert ctl.tg_prefs.chat_id is None
    out = run(svc, msg(f"/eslestir {code}"))
    assert ctl.tg_prefs.chat_id == CHAT and ctl.tg_prefs.chat_name == "@berk" and "Eşleşme tamam" in out[-1]
    assert svc.pairing is None                                       # single use


def test_pairing_brute_force_and_expiry():
    ctl, svc = service(paired=False)
    code = svc.begin_pairing()["code"]
    wrong = [f"{(int(code) + i) % 10 ** 6:06d}" for i in range(1, 11)]
    run(svc, *[msg(f"/eslestir {w}") for w in wrong])
    assert svc.pairing is None
    run(svc, msg(f"/eslestir {code}"))
    assert ctl.tg_prefs.chat_id is None                              # locked after 10 wrong codes
    ctl2, svc2 = service(paired=False)
    code2 = svc2.begin_pairing()["code"]
    svc2.pairing.expires = time.time() - 1
    run(svc2, msg(code2))
    assert ctl2.tg_prefs.chat_id is None


def test_report_and_history():
    ctl, svc = service()
    out = run(svc, msg("/rapor"), msg("/Geçmiş"), msg("/gecmis 7"), msg("/pozisyonlar"))
    r = out[0]
    assert "SOLUSDT" in r and "+1,40 $" in r and "+12,40 $" in r and "ETHUSDT" in r and "Bugün" in r
    assert "NEARUSDT" in out[1] and "+14,00 $" in out[1] and "Bugünkü işlemler" in out[1]
    assert "Son 7 günün" in out[2] and "SOLUSDT" in out[3]


def test_start_stop_and_turkish_letters():
    ctl, svc = service()
    out = run(svc, msg("/başlat"), msg("/DURDUR"))
    assert ctl.started == ["telegram"] and ctl.stopped == 1 and "çalışıyor" in out[0]


def test_close_needs_confirmation():
    ctl, svc = service()
    out = run(svc, msg("/kapat sol"))
    assert "/onay" in out[-1] and not ctl.closed
    run(svc, msg("/iptal"), msg("/onay"))
    assert not ctl.closed
    run(svc, msg("/kapat SOLUSDT"), msg("/onay"))
    assert ctl.closed == [(["SOLUSDT"], "Telegram'dan kapatıldı")]
    run(svc, msg("/hepsinikapat"))
    svc.pending = (svc.pending[0], svc.pending[1], time.time() - 1)       # confirmation too late
    out = run(svc, msg("/onay"))
    assert len(ctl.closed) == 1 and "Onay bekleyen" in out[-1]
    run(svc, msg("/hepsinikapat"), msg("/onay"))
    assert ctl.closed[-1][0] is None
    assert "pozisyonu yok" in run(svc, msg("/kapat BTC"))[-1]


def test_remote_control_can_be_switched_off():
    ctl, svc = service()
    ctl.tg_prefs.allow_control = False
    out = run(svc, msg("/baslat"), msg("/hepsinikapat"), msg("/rapor"))
    assert not ctl.started and "kapalı" in out[0] and "kapalı" in out[1] and "SOLUSDT" in out[2]


def test_old_and_flooding_commands_are_ignored():
    ctl, svc = service()
    run(svc, msg("/baslat", age=600))                                # queued while the app was closed
    assert not ctl.started
    run(svc, *[msg("/rapor") for _ in range(30)])
    assert len(svc.api.sent) == 20


def test_offset_is_persisted_so_a_restart_never_replays_commands():
    ctl, svc = service()
    svc.api.updates = [msg("/rapor"), msg("/rapor")]

    async def go():
        svc.task = asyncio.create_task(svc._run())
        await asyncio.sleep(0.2)
        svc.task.cancel()
    asyncio.run(go())
    off = ctl.store.get("telegram:offset")
    assert off and TelegramService(ctl, api_factory=FakeAPI).offset == off
    assert any(m == "setMyCommands" for m, _ in svc.api.calls)


def test_notifications():
    ctl, svc = service()

    async def go():
        svc.notify("trade_close", {"symbol": "SOLUSDT", "direction": 1, "entry": 140, "exit": 143, "pnl": 1.5,
                                   "pct": 2.4, "minutes": 75, "reason": "ÇIK sinyali (kâr al)", "mode": "live"})
        svc.notify("error", {"msg": "SOLUSDT stop emri kurulamadı"})
        svc.notify("error", {"msg": "SOLUSDT stop emri kurulamadı"})          # throttled
        svc.notify("net_down", {"minutes": 12})
        await asyncio.sleep(0.05)
    asyncio.run(go())
    t = [s["text"] for s in svc.api.sent]
    assert len(t) == 3 and "Pozisyon kapandı" in t[0] and "+1,50 $" in t[0] and "1 sa 15 dk" in t[0]
    assert all(s["parse_mode"] == "HTML" and s["chat_id"] == CHAT for s in svc.api.sent)
    ctl.tg_prefs.notify_trades = False
    svc.api.sent.clear()
    svc.notify("trade_open", {"symbol": "X", "direction": 1, "qty": 1, "price": 1, "margin": 1})
    assert not svc.outbox and not svc.api.sent


def test_report_escapes_html():
    snap = {"state": "running", "mode": "paper", "budget": 1, "margin_used": 0, "realized_today": 0,
            "positions": [], "external": ["<b>X</b>"]}
    assert "<b>X</b>" not in reports.report(snap, 0, {"trades": 0, "wins": 0})


# ---------------------------------------------------------------- end to end through the web API
def test_full_flow_through_the_app(tmp_path):
    FakeAPI.instances.clear()
    mp = tmp_path / "model.json"
    mp.write_text(json.dumps({"long": {"rules": [], "H": 6}, "short": {"rules": [], "H": 6}}))

    def eng(s, strategy, ctl):
        return Engine(s, Strat(0), Mkt(), PaperBroker(), ctl.store)
    app = create_app(tmp_path / "data", mp, {"telegram_api": FakeAPI, "engine": eng})
    st = app.state.tsa
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/api/auth/setup", json={"token": st.setup_token, "password": PW})
        H = {"x-csrf-token": r.headers["x-csrf-token"]}
        assert c.put("/api/telegram/token", json={"token": "nope"}, headers=H).status_code == 422
        v = c.put("/api/telegram/token", json={"token": TOKEN}, headers=H).json()
        assert v["configured"] and v["bot_username"] == "tsa_test_bot" and not v["paired"]
        assert c.post("/api/telegram/test", json={}, headers=H).status_code == 400
        code = c.post("/api/telegram/pair", json={}, headers=H).json()["code"]
        api = FakeAPI.instances[-1]
        api.updates.append(msg(f"/eslestir {code}"))
        for _ in range(100):
            if c.get("/api/telegram").json()["paired"]:
                break
            time.sleep(0.05)
        v = c.get("/api/telegram").json()
        assert v["paired"] and v["chat_name"] == "@berk"
        st.store.put("positions:paper", {s: asdict(pos(s)) for s in ("AAAUSDT", "BBBUSDT")})
        api.updates += [msg("/rapor"), msg("/hepsinikapat"), msg("/onay")]
        for _ in range(100):
            if any("Kapatıldı" in s["text"] for s in api.sent):
                break
            time.sleep(0.05)
        texts = [s["text"] for s in api.sent]
        assert any("AAAUSDT" in t and "Açık pozisyonlar (2)" in t for t in texts)
        assert any("Kapatıldı: AAAUSDT, BBBUSDT" in t for t in texts)
        assert not st.engine.positions and len(st.store.trades("paper")) == 2
        assert c.post("/api/telegram/test", json={}, headers=H).json() == {"ok": True}
        v = c.put("/api/telegram/prefs", json={"daily_summary": True, "daily_summary_hour": 9}, headers=H).json()
        assert v["prefs"]["daily_summary"] and v["prefs"]["daily_summary_hour"] == 9
        # the token is never returned and never stored in plain text
        blob = "".join(c.get(p).text for p in ("/api/telegram", "/api/status", "/api/app", "/api/settings", "/api/keys"))
        assert TOKEN not in blob and TOKEN.split(":")[1] not in blob
        for f in (tmp_path / "data").iterdir():
            if f.is_file():
                assert TOKEN.split(":")[1].encode() not in f.read_bytes(), f
        # after a restart the token comes back from the vault (login) and the pairing is kept
        app2 = create_app(tmp_path / "data", mp, {"telegram_api": FakeAPI, "engine": eng})
        st2 = app2.state.tsa
        assert st2.tg_token is None and st2.tg_prefs.chat_id == CHAT
        with TestClient(app2, base_url="http://127.0.0.1") as c2:
            assert c2.post("/api/auth/login", json={"password": PW}).status_code == 200
            assert st2.tg_token.reveal() == TOKEN
        v = c.request("DELETE", "/api/telegram", json={}, headers=H).json()
        assert not v["configured"] and not v["paired"]
    assert REDACT.clean(TOKEN) != TOKEN
