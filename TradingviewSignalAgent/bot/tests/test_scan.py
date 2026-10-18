"""Is the bot really scanning? Scan stats, today's signal / entry / skip counters, sleep and downtime
detection, keep-awake while the bot runs, and how all of it reaches the panel, Telegram and the tray."""
import asyncio
import ctypes
import types

import tsabot  # noqa: F401
from tsabot import reports, winsys
from tsabot.config import Settings
from tsabot.store import Store

from test_control import T0, Mkt, Strat, engine
from test_desktop import FakeTray, Srv, Win
from test_desktop import Ctl as ShellCtl
from test_telegram import msg, run, service

MIN = 60.0


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class SleepyMkt(Mkt):
    """Real-time market whose wall clock jumps by `jumps[i]` seconds while waiting for bar i."""
    realtime = True

    def __init__(self, clock, jumps):
        super().__init__()
        self.clock, self.jumps, self.i = clock, list(jumps), 0

    async def wait_next_close(self, stop):
        await super().wait_next_close(stop)
        if self.i >= len(self.jumps):          # every listed bar has been scanned
            stop.set()
            return
        self.clock.t += self.jumps[self.i]
        self.i += 1


def run_engine(e, jumps, clock):
    e.market = SleepyMkt(clock, jumps)
    e.wall = clock
    got = []
    e.listeners.append(lambda kind, data: got.append((kind, data)))

    async def go():
        await e.start()
        await e.task
    asyncio.run(go())
    return got


# ---------------------------------------------------------------- engine
def test_scan_stats_and_todays_signal_entry_skip_counters():
    s = Settings(symbols=["AAAUSDT", "BBBUSDT"], mode="paper", budget_usdt=100, max_positions=1)
    e = engine(settings=s, syms=("AAAUSDT", "BBBUSDT"))
    asyncio.run(e.on_bar())
    v = e.scan_view()
    assert v["last_ms"] == e._now() and v["coins"] == 2 and v["signals"] == 2
    assert v["signals_today"] == 2 and v["opened_today"] == 1 and v["skipped_today"] == 1
    assert "BBBUSDT sinyali atlandı" in v["last_skip"] and "eş zamanlı" in v["last_skip"]
    assert e.snapshot()["scan"]["opened_today"] == 1
    # after a restart today's counters come back from the event log
    e2 = engine(settings=s, store=e.store, syms=("AAAUSDT", "BBBUSDT"))
    v2 = e2.scan_view()
    assert (v2["signals_today"], v2["skipped_today"], v2["opened_today"]) == (2, 1, 1)
    assert v2["last_skip"] == v["last_skip"]


def test_skipped_signals_have_their_own_level():
    s = Settings(symbols=["AAAUSDT"], mode="paper", budget_usdt=100, max_positions=1, daily_loss_limit_pct=1)
    e = engine(settings=s)
    e.store.add_trade({"mode": "paper", "symbol": "XUSDT", "direction": 1, "qty": 1, "entry_time": T0,
                       "entry_price": 1, "exit_time": T0 + 1, "exit_price": 1, "reason": "x", "fees": 0, "pnl": -5,
                       "margin": 1, "rule": 0})
    asyncio.run(e.on_bar())
    ev = e.store.events(10)
    assert any(x["level"] == "skip" and "Günlük zarar limiti" in x["msg"] for x in ev)
    assert not e.positions and e.scan_view()["skipped_today"] == 1


def test_sleep_gap_is_detected_reported_and_scanning_continues():
    clock = Clock()
    e = engine(strat=Strat(d=0))
    got = run_engine(e, [300, 300, 47 * MIN, 300], clock)
    gaps = [d for k, d in got if k == "gap"]
    assert len(gaps) == 1 and gaps[0]["minutes"] == 47 and gaps[0]["cause"] == "sleep"
    assert gaps[0]["until"] - gaps[0]["since"] == 47 * 60_000
    ev = [x["msg"] for x in e.store.events(50) if x["level"] == "warn"]
    assert any("47 dk boyunca tarama yapamadı" in m and "uykuya" in m for m in ev)
    assert e.bar_count == 4                                     # every bar was still scanned
    assert e.snapshot()["scan"]["gap"]["minutes"] == 47
    assert ("state", {"state": "running", "mode": "paper"}) in got
    # a clean stop leaves no heartbeat: the next start does not report "downtime"
    assert e.store.get("heartbeat:paper") is None


def test_normal_bars_and_slow_scans_are_no_gap():
    clock = Clock()
    e = engine(strat=Strat(d=0))
    got = run_engine(e, [300, 303, 290, 10 * MIN], clock)
    assert not [k for k, _ in got if k == "gap"]


def test_downtime_after_a_crash_or_power_cut_is_reported_at_start():
    clock = Clock()
    store = Store(":memory:")
    store.put("heartbeat:paper", int((clock.t - 2 * 3600 - 5 * MIN) * 1000))   # last scan, then no clean stop
    e = engine(strat=Strat(d=0), store=store)
    got = run_engine(e, [300], clock)
    gaps = [d for k, d in got if k == "gap"]
    assert gaps and gaps[0]["cause"] == "down" and gaps[0]["minutes"] == 125
    assert any("2 sa 5 dk çalışmadı" in x["msg"] for x in e.store.events(20))


def test_replay_mode_never_reports_gaps():
    e = engine(strat=Strat(d=0))
    e.wall = Clock()
    e.loop_wall = e.wall.t - 3600
    assert e.market.realtime is False
    asyncio.run(e.on_bar())
    assert e.last_gap is None and e.store.get("heartbeat:paper") is None


# ---------------------------------------------------------------- texts
def test_scan_line_in_the_telegram_report():
    snap = {"state": "running", "scan": {"last_ms": 1_000_000, "coins": 36, "signals_today": 4, "opened_today": 1,
                                         "skipped_today": 3, "last_skip": "BTCUSDT sinyali atlandı: <bütçe>"}}
    ok = reports.scan_line(snap, 1_000_000 + 2 * 60_000)
    assert "36 coin" in ok and "2 dk önce" in ok and "4 sinyal · 1 pozisyon açıldı · 3 atlandı" in ok
    assert "&lt;bütçe&gt;" in ok and "gecikti" not in ok
    late = reports.scan_line(snap, 1_000_000 + 40 * 60_000)
    assert "⚠️" in late and "Tarama gecikti" in late
    assert reports.scan_line({**snap, "state": "stopped"}, 0) is None
    first = reports.scan_line({"state": "running", "scan": {"last_ms": None}}, 0)
    assert "ilk tarama" in first


def test_rapor_shows_the_scan_and_gap_goes_to_the_phone():
    ctl, svc = service()
    ctl.snap = {**ctl.snap, "scan": {"last_ms": ctl.now_ms() - 60_000, "coins": 36, "signals_today": 0,
                                     "opened_today": 0, "skipped_today": 0, "last_skip": None}}
    out = run(svc, msg("/rapor"))
    assert "Son tarama" in out[0] and "36 coin" in out[0]

    async def go():
        svc.notify("gap", {"minutes": 95, "since": 0, "until": 95 * 60_000, "cause": "sleep", "mode": "paper"})
        svc.notify("gap", {"minutes": 30, "since": 0, "until": 30 * 60_000, "cause": "down", "mode": "paper"})
        await asyncio.sleep(0.05)
    asyncio.run(go())
    t = [s["text"] for s in svc.api.sent[1:]]
    assert "1 sa 35 dk boyunca tarama yapamadı" in t[0] and "uykuya" in t[0]
    assert "30 dk çalışmadı" in t[1]


# ---------------------------------------------------------------- desktop: keep awake + state
def shell(monkeypatch, engine_state=None):
    from tsabot.desktop import DesktopShell
    calls = []
    monkeypatch.setattr(winsys, "keep_awake", lambda on: calls.append(on) or on)
    ctl = ShellCtl()
    if engine_state:
        ctl.engine = types.SimpleNamespace(state=engine_state)
    s = DesktopShell(Srv(ctl), "http://127.0.0.1:1/", pystray_mod=FakeTray)
    s.window = Win()
    return s, calls


def test_a_bot_resumed_at_start_up_keeps_the_computer_awake(monkeypatch):
    s, calls = shell(monkeypatch)
    # the controller resumes the bot while the server starts, before the window / tray exist
    for fn in s.ctl.hooks:
        fn("state", {"state": "starting"})
    assert s.state == "starting" and calls[-1] is True


def test_state_sync_repairs_missed_events_and_follows_the_preference(monkeypatch):
    s, calls = shell(monkeypatch, engine_state="running")
    assert s.state == "stopped"
    s.sync_state()
    assert s.state == "running" and calls[-1] is True
    s.ctl.app_prefs.prevent_sleep = False
    for fn in s.ctl.prefs_hooks:
        fn(s.ctl.app_prefs)
    assert calls[-1] is False
    s.ctl.app_prefs.prevent_sleep = True
    s.ctl.engine.state = "stopped"
    s.sync_state()
    assert s.state == "stopped" and calls[-1] is False


def test_gap_event_shows_a_tray_notice(monkeypatch):
    s, _ = shell(monkeypatch)
    s.start_tray()
    s.on_event("gap", {"minutes": 52, "cause": "sleep"})
    assert "52 dk tarama yapamadı" in s.icon.notes[-1]


# ---------------------------------------------------------------- winsys with a fake kernel32
class FakeK32:
    def __init__(self, create=1234, set_ok=True):
        self.calls, self.create, self.set_ok = [], create, set_ok

    def PowerCreateRequest(self, ctx):
        c = ctx._obj
        self.calls.append(("create", c.Version, c.Flags, c.Reason))
        return self.create

    def PowerSetRequest(self, h, t):
        self.calls.append(("set", h, t))
        return self.set_ok

    def PowerClearRequest(self, h, t):
        self.calls.append(("clear", h, t))
        return 1

    def SetThreadExecutionState(self, flags):
        self.calls.append(("es", flags))
        return 1

    def GetCurrentProcess(self):
        return -1

    def SetProcessInformation(self, h, cls, ptr, size):
        st = ptr._obj
        self.calls.append(("spi", h, cls, st.Version, st.ControlMask, st.StateMask, size))
        return 1

    def GetSystemPowerStatus(self, ptr):
        st = ptr._obj
        st.ACLineStatus, st.BatteryFlag, st.BatteryLifePercent = self.power
        return 1


def test_keep_awake_uses_a_thread_independent_power_request():
    k = FakeK32()
    ka = winsys.KeepAwake(k)
    assert ka.set(True) and ka.method == "power_request"
    assert k.calls[0] == ("create", 0, 1, winsys.KeepAwake.REASON)
    assert ("set", 1234, 1) in k.calls and ("set", 1234, 3) in k.calls      # system + execution required
    n = len(k.calls)
    assert ka.set(True) and len(k.calls) == n                             # idempotent
    assert ka.set(False) is False
    assert k.calls[-2:] == [("clear", 1234, 3), ("clear", 1234, 1)]
    ka.set(True)
    assert sum(1 for c in k.calls if c[0] == "create") == 1                # the request object is reused


def test_keep_awake_falls_back_to_the_thread_state():
    k = FakeK32(create=0)
    ka = winsys.KeepAwake(k)
    assert ka.set(True) and ka.method == "thread_state" and ("es", 0x80000001) in k.calls
    ka.set(False)
    assert k.calls[-1] == ("es", 0x80000000)


def test_keep_awake_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(winsys, "IS_WIN", False)
    assert winsys.keep_awake(True) is False


def test_power_throttling_is_switched_off_for_the_process():
    k = FakeK32()
    assert winsys.disable_power_throttling(k)
    _, h, cls, ver, control, state, size = k.calls[-1]
    assert (cls, ver, control, state, size) == (4, 1, 0x1 | 0x4, 0, 3 * ctypes.sizeof(ctypes.c_ulong))


def test_power_status():
    k = FakeK32()
    k.power = (0, 1, 57)
    assert winsys.power_status(k) == {"ac": False, "battery": 57}
    k.power = (1, 128, 255)                                               # desktop PC without a battery
    assert winsys.power_status(k) == {"ac": True, "battery": None}
