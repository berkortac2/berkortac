"""TSA Bot desktop application: own window + system-tray icon (no browser needed).

* Closing the window (X) only hides it: the bot keeps running and the icon stays in the tray
  (Windows: the ^ "hidden icons" area next to the clock).
* Tray menu: show window, start / stop the bot, "Tamamen kapat" (quit completely).
* One instance per user; starting it again brings the running window to the front.
* Optional: start with Windows, resume the bot after a restart, keep the computer awake.

Run from the sources:  pythonw TSABot.pyw   (or  python -m tsabot.desktop)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

log = logging.getLogger("tsabot.desktop")
TITLE = "TSA Bot"


# ------------------------------------------------------------------ logging
def setup_logging(data_dir: Path) -> Path:
    from .secrets import REDACT, install_redaction
    logdir = data_dir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / "tsabot.log"
    h = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    h.addFilter(REDACT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    if sys.stderr is not None and not getattr(sys, "frozen", False):
        sh = logging.StreamHandler()
        sh.addFilter(REDACT)
        root.addHandler(sh)
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
    install_redaction()

    def hook(t, v, tb):
        log.error("unhandled: %s", "".join(traceback.format_exception(t, v, tb))[-4000:])
    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    return path


def free_port(preferred: int = 8765) -> int:
    for p in [preferred] + list(range(preferred + 1, preferred + 20)):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ------------------------------------------------------------------ server in a background thread
class ServerThread:
    """uvicorn + the bot's asyncio loop, in one background thread (the GUI owns the main thread)."""

    def __init__(self, app, port: int):
        import uvicorn
        self.app, self.port = app, port
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, log_level="warning",
                             server_header=False, lifespan="on")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, name="tsabot-server", daemon=True)

    def start(self, timeout: float = 60.0) -> None:
        self.thread.start()
        t0 = time.time()
        while not self.server.started:
            if not self.thread.is_alive() or time.time() - t0 > timeout:
                raise RuntimeError("Sunucu başlatılamadı (port kullanımda olabilir)")
            time.sleep(0.05)

    @property
    def ctl(self):
        return self.app.state.tsa

    def call(self, coro, timeout: float | None = 120):
        """Run a coroutine in the bot's loop from another thread and wait for its result."""
        loop = self.ctl.loop
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def stop(self, timeout: float = 110) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)


# ------------------------------------------------------------------ tray icon image
def icon_image(state: str = "stopped", size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    top, bot = (124, 131, 255), (34, 211, 238)
    for y in range(s):                                   # vertical gradient, rounded square
        t = y / (s - 1)
        c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
        d.line([(0, y), (s, y)], fill=c)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.24), fill=255)
    img.putalpha(mask)
    c = s / 2
    r = s * 0.26
    d.polygon([(c, c - r), (c + r, c), (c, c + r), (c - r, c)], fill=(255, 255, 255, 235))
    dot = {"running": (34, 197, 94), "draining": (245, 165, 36), "starting": (245, 165, 36)}.get(state, (148, 163, 184))
    rr = s * 0.17
    d.ellipse([s - 2 * rr - 2, s - 2 * rr - 2, s - 2, s - 2], fill=dot + (255,), outline=(15, 23, 42, 255),
              width=max(1, s // 32))
    return img


# ------------------------------------------------------------------ the shell
class DesktopShell:
    """Window + tray behaviour. GUI toolkits are injected, so the logic is unit-tested without a screen."""

    def __init__(self, server: ServerThread, url: str, start_hidden: bool = False, webview_mod=None,
                 pystray_mod=None, confirm=None):
        self.server, self.url, self.start_hidden = server, url, start_hidden
        self.webview, self.pystray = webview_mod, pystray_mod
        self.window = None
        self.icon = None
        self.quitting = False
        self.hidden_hint_shown = False
        self.tray_ok = False
        self.state = "stopped"
        self.confirm = confirm
        self.done = threading.Event()

    @property
    def ctl(self):
        return self.server.ctl

    # ---------------------------------------------------------- window events
    def on_closing(self) -> bool:
        """X button: hide instead of quitting (return False cancels the close)."""
        if self.quitting:
            return True
        if not self.tray_ok:               # no tray icon on this system: closing must really quit
            self.quitting = True           # (open positions keep their exchange stops)
            threading.Thread(target=self._shutdown, name="tsabot-quit", daemon=True).start()
            return True
        self.hide()
        if not self.hidden_hint_shown:
            self.hidden_hint_shown = True
            self.notify("TSA Bot arka planda çalışmaya devam ediyor. Tamamen kapatmak için saatin yanındaki "
                        "simgeye sağ tıklayıp 'Tamamen kapat'ı seç.")
        return False

    def show(self, *_):
        if self.window is not None:
            try:
                self.window.show()
                self.window.restore()
            except Exception:
                pass
        else:
            import webbrowser
            webbrowser.open(self.url)

    def hide(self, *_):
        if self.window is not None:
            try:
                self.window.hide()
            except Exception:
                pass

    # ---------------------------------------------------------- bot actions (tray)
    def start_bot(self, *_):
        try:
            msg = self.server.call(self.ctl.remote_start("tray"))
            self.notify(msg.replace("<b>", "").replace("</b>", ""))
        except Exception as e:
            self.notify(f"Başlatılamadı: {e}")

    def stop_bot(self, *_):
        try:
            msg = self.server.call(self.ctl.remote_stop())
            self.notify(msg.split("\n")[0])
        except Exception as e:
            self.notify(f"Durdurulamadı: {e}")

    def open_positions(self) -> int:
        try:
            return len(self.ctl.snapshot().get("positions") or [])
        except Exception:
            return 0

    def quit(self, *_) -> bool:
        """'Tamamen kapat': stop the bot loop, the server, the tray and the window."""
        n = self.open_positions()
        if n and not self._confirm(
                f"{n} açık pozisyon var. Uygulama kapanınca bu pozisyonları borsadaki stop emirleri korumaya devam "
                "eder, ama ÇIK sinyali ve süre çıkışı takip edilmez.\n\nBot çalışıyorsa bir sonraki açılışta "
                "kaldığı yerden devam eder. Tamamen kapatılsın mı?"):
            return False
        self.quitting = True
        threading.Thread(target=self._shutdown, name="tsabot-quit", daemon=True).start()
        return True

    def _confirm(self, text: str) -> bool:
        if self.confirm is not None:
            return bool(self.confirm(text))
        if self.window is not None:
            try:
                self.show()
                return bool(self.window.create_confirmation_dialog("TSA Bot'u kapat", text))
            except Exception:
                return True
        return True

    def _shutdown(self):
        try:
            self.server.stop()
        finally:
            if self.icon is not None:
                try:
                    self.icon.stop()
                except Exception:
                    pass
            if self.window is not None:
                try:
                    self.window.destroy()
                except Exception:
                    pass
            self.done.set()

    # ---------------------------------------------------------- notifications / icon
    def notify(self, text: str, title: str = TITLE):
        log.info("notify: %s", text)
        if self.icon is not None and self.tray_ok:
            try:
                self.icon.notify(text[:250], title)
            except Exception:
                pass

    def on_event(self, kind: str, data: dict):
        """Controller hook (runs in the bot's thread)."""
        from . import winsys
        from .reports import money
        prefs = self.ctl.app_prefs
        if kind == "state":
            self.state = data.get("state", "stopped")
            if prefs.prevent_sleep:
                winsys.keep_awake(self.state in ("running", "draining", "starting"))
            self.refresh_icon()
        elif kind == "stopped":
            self.state = "stopped"
            winsys.keep_awake(False)
            self.refresh_icon()
        elif kind == "trade_open" and prefs.notify_desktop:
            self.notify(f"{data['symbol']} {'LONG' if data['direction'] > 0 else 'SHORT'} açıldı · marjin "
                        f"{data['margin']:.2f} $")
        elif kind == "trade_close" and prefs.notify_desktop:
            self.notify(f"{data['symbol']} kapandı · {money(data['pnl'])} · {data.get('reason', '')}")
        elif kind == "error" and prefs.notify_desktop:
            self.notify(str(data.get("msg", ""))[:200])

    def status_text(self, *_) -> str:
        try:
            snap = self.ctl.snapshot()
            st = {"running": "Çalışıyor", "draining": "Yeni işlem kapalı", "starting": "Başlatılıyor"}.get(
                snap.get("state"), "Durdu")
            n = len(snap.get("positions") or [])
            u = sum(float(p.get("unrealized") or 0) for p in snap.get("positions") or [])
            return f"{st} · {n} pozisyon" + (f" · {u:+.2f} $" if n else "")
        except Exception:
            return "Durum bilinmiyor"

    def refresh_icon(self):
        if self.icon is None:
            return
        try:
            self.icon.icon = icon_image(self.state)
            self.icon.title = f"{TITLE} — {self.status_text()}"
            self.icon.update_menu()
        except Exception:
            pass

    # ---------------------------------------------------------- tray
    def build_tray(self):
        ps = self.pystray
        running = lambda item: self.state in ("running", "starting")          # noqa: E731
        menu = ps.Menu(
            ps.MenuItem("TSA Bot'u göster", self.show, default=True),
            ps.MenuItem(lambda item: self.status_text(), None, enabled=False),
            ps.Menu.SEPARATOR,
            ps.MenuItem("Botu başlat", self.start_bot, enabled=lambda item: not running(item)),
            ps.MenuItem("Botu durdur (yeni işlem açma)", self.stop_bot, enabled=running),
            ps.Menu.SEPARATOR,
            ps.MenuItem("Tamamen kapat", lambda *_: self.quit()),
        )
        self.icon = ps.Icon("TSABot", icon_image(self.state), TITLE, menu)
        return self.icon

    def start_tray(self) -> bool:
        if self.pystray is None:
            return False
        try:
            icon = self.build_tray()
            if sys.platform == "darwin":
                return False                               # needs the main thread there; not supported
            threading.Thread(target=icon.run, name="tsabot-tray", daemon=True).start()
            self.tray_ok = True
        except Exception as e:
            log.warning("tray icon unavailable: %s", e)
            self.tray_ok = False
        return self.tray_ok

    def watch_show_requests(self, flag: Path):
        """A second start of the app drops a file here: bring the window to the front."""
        def loop():
            while not self.done.is_set():
                if flag.exists():
                    try:
                        flag.unlink()
                    except OSError:
                        pass
                    self.show()
                self.done.wait(1.0)
        threading.Thread(target=loop, name="tsabot-show", daemon=True).start()

    # ---------------------------------------------------------- main loop
    def run(self, fragment: str = "", after=None) -> None:
        """Blocks until the app quits. `after` (optional) runs in a thread once the window exists."""
        self.ctl.hooks.append(self.on_event)
        self.start_tray()
        wv = self.webview
        if wv is not None:
            try:
                wv.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
                wv.settings["ALLOW_DOWNLOADS"] = False
            except Exception:
                pass
            try:
                self.window = wv.create_window(TITLE, self.url + fragment, width=1400, height=880, min_size=(980, 640),
                                               background_color="#070a12", hidden=self.start_hidden and self.tray_ok,
                                               text_select=True)
                self.window.events.closing += self.on_closing
                if after is not None:
                    wv.start(after, private_mode=True)
                else:
                    wv.start(private_mode=True)
                self.done.set()
                return
            except Exception as e:
                log.warning("window could not be opened, using the browser: %s", e)
                self.window = None
        import webbrowser                                     # fallback: browser + tray
        if not self.start_hidden:
            webbrowser.open(self.url + fragment)
        if not self.tray_ok and after is None:
            from . import winsys
            winsys.message_box("TSA Bot çalışıyor. Arayüz tarayıcıda açıldı: " + self.url)
        if after is not None:
            threading.Thread(target=after, name="tsabot-after", daemon=True).start()
        self.done.wait()


def gui_check(shell: DesktopShell, out_path: Path) -> None:
    """--guitest: the real window and tray icon of the packaged app (run on Windows in CI).
    Page loaded and rendered, X hides to the tray instead of quitting, window shows again, 'quit' works."""
    res = {"ok": False, "steps": [], "errors": []}

    def step(name, ok):
        (res["steps"] if ok else res["errors"]).append(name)
    try:
        w = shell.window
        step("window created", w is not None)
        if w is not None:
            ev = w.events.loaded
            t0 = time.time()
            while time.time() - t0 < 60 and not (ev.is_set() if hasattr(ev, "is_set") else False):
                time.sleep(0.2)
            step("page loaded", ev.is_set() if hasattr(ev, "is_set") else True)
            seen = None
            for _ in range(60):
                try:
                    seen = w.evaluate_js("document.title + '|' + (document.getElementById('setup-form')"
                                         ".classList.contains('hidden') ? 'login' : 'setup')")
                    if seen == "TSA Bot|setup":
                        break
                except Exception as e:
                    seen = f"error {type(e).__name__}: {e}"
                time.sleep(0.5)
            step(f"ui rendered ({seen})", seen == "TSA Bot|setup")
        step("tray icon", shell.tray_ok)
        step("X hides to the tray", shell.on_closing() is False and not shell.quitting and not shell.done.is_set())
        shell.show()
        time.sleep(1.0)
        step("window shown again", not shell.done.is_set())
        res["ok"] = not res["errors"]
    except Exception:
        res["errors"].append(traceback.format_exc()[-2000:])
    finally:
        out_path.write_text(json.dumps(res, indent=1), encoding="utf-8")
        shell.quit()                                      # no open positions: quits without a question


# ------------------------------------------------------------------ self test (CI / support)
def selftest(data_dir: Path) -> int:
    """Starts the real server, runs the first-run flow and the model once; writes selftest.json."""
    import httpx
    out = {"ok": False, "steps": []}
    srv = None
    try:
        import numpy as np
        import pandas as pd
        from .api import create_app
        from .secrets import Protector, Vault
        from .strategy import Strategy
        from .config import MODEL_PATH
        app = create_app(data_dir / "selftest", desktop=True)
        srv = ServerThread(app, free_port(18765))
        srv.start()
        base = f"http://127.0.0.1:{srv.port}"
        with httpx.Client(base_url=base, timeout=30) as c:
            assert c.get("/").status_code == 200 and c.get("/static/app.js").status_code == 200
            out["steps"].append("ui served")
            st = c.get("/api/auth/state").json()
            assert st["setup_required"] and st["desktop"]
            r = c.post("/api/auth/setup", json={"token": app.state.tsa.setup_token, "password": "selftest-password-1"})
            assert r.status_code == 200, r.text
            H = {"x-csrf-token": r.headers["x-csrf-token"]}
            r = c.put("/api/settings", json={"mode": "paper", "leverage": 2, "emergency_stop_pct": 5}, headers=H)
            assert r.status_code == 200 and r.json()["leverage"] == 2, r.text
            assert c.get("/api/status").json()["mode"] == "paper"
            assert c.get("/api/model").json().get("long")
            out["steps"].append("api ok")
        rng = np.random.default_rng(1)
        n = 700
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
        t0 = 1_700_000_000_000
        k5 = pd.DataFrame({"open_time": t0 + np.arange(n) * 300_000, "open": close, "high": close * 1.002,
                           "low": close * 0.998, "close": close, "volume": rng.uniform(1, 2, n),
                           "close_time": t0 + np.arange(n) * 300_000 + 299_999})
        h = k5.iloc[::12].reset_index(drop=True).copy()
        h["open_time"] = t0 + np.arange(len(h)) * 3_600_000
        sig = Strategy.load(MODEL_PATH).evaluate("BTCUSDT", k5, h)
        assert sig is not None and np.isfinite(sig.atr)
        out["steps"].append("model + numba ok")
        v = Vault(data_dir / "selftest" / "vault-test.json")
        v.save("pw-selftest-123", "A" * 20, "B" * 20)
        assert v.load("pw-selftest-123")[1].reveal() == "B" * 20
        p = Protector()
        if p.available:
            assert p.unprotect(p.protect(b"secret-data")) == b"secret-data"
            out["steps"].append("dpapi ok")
        icon_image("running")
        if sys.platform == "win32" or os.environ.get("DISPLAY"):
            for mod in ("webview", "pystray"):          # pystray needs a desktop session
                __import__(mod)
            out["steps"].append("gui modules ok")
        out["ok"] = True
    except Exception:
        out["error"] = traceback.format_exc()[-3000:]
    finally:
        if srv is not None:
            srv.stop(30)
    (data_dir / "selftest.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0 if out["ok"] else 1


# ------------------------------------------------------------------ entry point
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TSA Bot masaüstü uygulaması")
    ap.add_argument("--minimized", action="store_true", help="pencereyi açmadan sistem tepsisinde başla")
    ap.add_argument("--browser", action="store_true", help="kendi penceresi yerine tarayıcıda aç")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--guitest", action="store_true", help="pencere + tepsi testi (CI)")
    a = ap.parse_args(argv)

    from .config import default_data_dir
    data_dir = Path(a.data_dir) if a.data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    # numba's compiled-function cache must be writable (the program folder may not be)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(data_dir / "numba_cache"))
    setup_logging(data_dir)
    if a.selftest:
        return selftest(data_dir)

    from . import winsys
    lock = winsys.SingleInstance(data_dir / "app.lock")
    show_flag = data_dir / "show.request"
    if not lock.acquire():
        show_flag.write_text(str(time.time()))            # the running copy shows its window
        return 0

    from .api import create_app
    from .secrets import Protector
    app = create_app(data_dir, protector=Protector(), desktop=True)
    ctl = app.state.tsa

    def apply_prefs(p):
        if winsys.IS_WIN:
            winsys.set_autostart(p.autostart)
    ctl.prefs_hooks.append(apply_prefs)
    if winsys.IS_WIN and ctl.app_prefs.autostart:
        winsys.set_autostart(True)                        # keep the path right after the app was moved

    srv = ServerThread(app, free_port(a.port))
    srv.start()
    url = f"http://127.0.0.1:{srv.port}/"
    frag = f"#setup={ctl.setup_token}" if ctl.setup_token else f"#desk={ctl.desktop_token}"
    log.info("TSA Bot %s", url)

    webview_mod = pystray_mod = None
    if not a.browser:
        try:
            import webview as webview_mod
        except Exception as e:
            log.warning("pywebview unavailable: %s", e)
    try:
        import pystray as pystray_mod
    except Exception as e:
        log.warning("pystray unavailable: %s", e)
    shell = DesktopShell(srv, url, start_hidden=a.minimized, webview_mod=webview_mod, pystray_mod=pystray_mod)
    shell.watch_show_requests(show_flag)
    gui_out = data_dir / "guitest.json"
    after = None
    if a.guitest:
        def watchdog():                                   # never hang a CI job
            gui_out.write_text(json.dumps({"ok": False, "errors": ["timeout"]}), encoding="utf-8")
            os._exit(3)
        threading.Timer(180, watchdog).start()
        after = lambda: gui_check(shell, gui_out)         # noqa: E731
    try:
        shell.run(frag, after=after)
    finally:
        if not shell.quitting:                            # window closed without the tray (e.g. no tray support)
            shell.quitting = True
            shell._shutdown()
        lock.release()
    if a.guitest:
        ok = gui_out.exists() and json.loads(gui_out.read_text(encoding="utf-8")).get("ok")
        os._exit(0 if ok else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
