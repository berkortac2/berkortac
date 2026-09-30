"""Desktop shell: X hides to the tray, tray menu actions, 'Tamamen kapat' with confirmation, single instance,
Windows start-up entry, icon, real server thread, self test."""
import asyncio
import json
import threading
import time
import types

import pytest

import tsabot  # noqa: F401
from tsabot import winsys
from tsabot.config import AppPrefs
from tsabot.desktop import DesktopShell, ServerThread, free_port, icon_image


class Win:
    def __init__(self):
        self.calls = []
        self.events = types.SimpleNamespace(closing=None)

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def restore(self):
        self.calls.append("restore")

    def destroy(self):
        self.calls.append("destroy")

    def create_confirmation_dialog(self, title, text):
        self.calls.append(("confirm", text))
        return self.answer


class Icon:
    def __init__(self, name, image, title, menu):
        self.name, self.icon, self.title, self.menu = name, image, title, menu
        self.notes, self.stopped, self.updates = [], False, 0

    def run(self):
        pass

    def stop(self):
        self.stopped = True

    def notify(self, text, title):
        self.notes.append(text)

    def update_menu(self):
        self.updates += 1


class MenuItem:
    def __init__(self, text, action, default=False, enabled=True):
        self.text, self.action, self.default, self.enabled = text, action, default, enabled


class Menu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


FakeTray = types.SimpleNamespace(Icon=Icon, MenuItem=MenuItem, Menu=Menu)


class Ctl:
    def __init__(self, positions=0):
        self.app_prefs = AppPrefs()
        self.hooks = []
        self.n = positions
        self.started, self.stopped = 0, 0

    def snapshot(self):
        return {"state": "running", "positions": [{"unrealized": 1.0}] * self.n}

    async def remote_start(self, src):
        self.started += 1
        return "▶️ Bot çalışıyor"

    async def remote_stop(self):
        self.stopped += 1
        return "⏸ Yeni işlem açma durduruldu.\nDetay"


class Srv:
    def __init__(self, ctl):
        self.ctl = ctl
        self.stopped = False

    def call(self, coro, timeout=None):
        return asyncio.run(coro)

    def stop(self, timeout=None):
        self.stopped = True


def shell(positions=0, tray=True):
    s = DesktopShell(Srv(Ctl(positions)), "http://127.0.0.1:1/", pystray_mod=FakeTray if tray else None)
    s.window = Win()
    s.start_tray()
    return s


def test_x_button_hides_to_the_tray_and_keeps_running():
    s = shell()
    assert s.tray_ok and s.on_closing() is False
    assert s.window.calls == ["hide"] and not s.server.stopped
    assert "arka planda" in s.icon.notes[0]
    s.on_closing()
    assert len(s.icon.notes) == 1                        # the hint is shown once


def test_without_a_tray_the_x_button_really_quits():
    s = shell(tray=False)
    assert s.on_closing() is True
    s.done.wait(5)
    assert s.server.stopped


def test_tray_menu_has_show_start_stop_and_quit():
    s = shell()
    texts = [i.text for i in s.icon.menu.items if isinstance(i, MenuItem) and isinstance(i.text, str)]
    assert texts == ["TSA Bot'u göster", "Botu başlat", "Botu durdur (yeni işlem açma)", "Tamamen kapat"]
    default = [i for i in s.icon.menu.items if isinstance(i, MenuItem) and i.default][0]
    default.action()
    assert s.window.calls[-2:] == ["show", "restore"]
    s.start_bot()
    s.stop_bot()
    assert s.ctl.started == 1 and s.ctl.stopped == 1 and s.icon.notes[-1] == "⏸ Yeni işlem açma durduruldu."


def test_quit_asks_when_positions_are_open():
    s = shell(positions=2)
    s.window.answer = False
    assert s.quit() is False and not s.quitting and not s.server.stopped
    assert "2 açık pozisyon" in s.window.calls[-1][1]
    s.window.answer = True
    assert s.quit() is True
    s.done.wait(5)
    assert s.server.stopped and s.icon.stopped and "destroy" in s.window.calls
    assert s.on_closing() is True                        # while quitting the window may close


def test_quit_without_positions_needs_no_confirmation():
    s = shell(positions=0)
    s.window.answer = False
    assert s.quit() is True


def test_events_update_icon_and_notify():
    s = shell()
    s.on_event("state", {"state": "running"})
    assert s.state == "running" and s.icon.updates >= 1 and "Çalışıyor" in s.icon.title
    s.on_event("trade_close", {"symbol": "SOLUSDT", "pnl": -1.234, "reason": "Zarar kes (SL)"})
    assert "SOLUSDT kapandı" in s.icon.notes[-1] and "−1,23 $" in s.icon.notes[-1]
    s.ctl.app_prefs.notify_desktop = False
    n = len(s.icon.notes)
    s.on_event("trade_open", {"symbol": "X", "direction": 1, "margin": 1.0})
    assert len(s.icon.notes) == n


@pytest.mark.parametrize("state", ["running", "draining", "stopped"])
def test_icon_image(state):
    im = icon_image(state)
    assert im.size == (64, 64) and im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0   # rounded corners


def test_second_instance_is_refused(tmp_path):
    a, b = winsys.SingleInstance(tmp_path / "x.lock"), winsys.SingleInstance(tmp_path / "x.lock")
    assert a.acquire() and not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_show_request_from_a_second_start(tmp_path):
    s = shell()
    flag = tmp_path / "show.request"
    s.watch_show_requests(flag)
    flag.write_text("1")
    for _ in range(50):
        if not flag.exists():
            break
        time.sleep(0.05)
    s.done.set()
    assert not flag.exists() and "show" in s.window.calls


class FakeReg:
    HKEY_CURRENT_USER, KEY_SET_VALUE, KEY_QUERY_VALUE, REG_SZ = 1, 2, 4, 1

    def __init__(self):
        self.values = {}

    class _K:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path, res, access):
        assert path == winsys.RUN_KEY
        return self._K()

    def SetValueEx(self, k, name, res, typ, val):
        self.values[name] = val

    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], 1


def test_windows_startup_entry():
    r = FakeReg()
    assert winsys.set_autostart(True, '"C:\\TSABot\\TSABot.exe" --minimized', winreg_mod=r)
    assert r.values["TSABot"].endswith("--minimized")
    assert not winsys.set_autostart(False, winreg_mod=r) and "TSABot" not in r.values
    assert not winsys.set_autostart(False, winreg_mod=r)             # removing twice is fine


def test_launch_command_points_to_the_launcher():
    cmd = winsys.launch_command(True)
    assert "TSABot.pyw" in cmd and cmd.endswith("--minimized")


def test_real_server_thread_and_controller_calls(tmp_path):
    from tsabot.api import create_app
    mp = tmp_path / "m.json"
    mp.write_text(json.dumps({"long": {"rules": [], "H": 6}, "short": {"rules": [], "H": 6}}))
    app = create_app(tmp_path / "d", mp, desktop=True)
    srv = ServerThread(app, free_port(19000))
    srv.start()
    try:
        import httpx
        st = httpx.get(f"http://127.0.0.1:{srv.port}/api/auth/state").json()
        assert st["desktop"] and st["setup_required"]
        assert srv.call(srv.ctl.remote_stop()).startswith("⏹")
        assert threading.current_thread() is threading.main_thread()
    finally:
        srv.stop(30)
    assert not srv.thread.is_alive()


def test_selftest(tmp_path):
    from tsabot.desktop import selftest
    rc = selftest(tmp_path)
    out = json.loads((tmp_path / "selftest.json").read_text())
    assert rc == 0, out.get("error")
    assert out["ok"] and "model + numba ok" in out["steps"]
