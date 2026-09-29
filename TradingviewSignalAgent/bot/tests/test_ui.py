"""Browser tests (Playwright + Chromium): setup, login, settings, replay trading, XSS safety."""
import json
import os
import re
import socket
import threading
import time

import pytest
import uvicorn

import tsabot
from tsabot.api import create_app

pw = pytest.importorskip("playwright.sync_api")
PW = "ui-test-password-123"
OUT = tsabot.BOT_ROOT / "tests" / "screenshots"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if not (tsabot.REPO_ROOT / "data" / "raw" / "NEARUSDT_5m.parquet").exists():
        pytest.skip("research data not downloaded")
    tmp = tmp_path_factory.mktemp("ui")
    model = tmp / "model.json"
    model.write_text(json.dumps({"name": "UI test model",
                                 "long": {"rules": [[["rsi7", "<=", -0.35]]], "H": 6, "tp_atr": 1.5, "sl_atr": 1.0},
                                 "short": {"rules": [[["rsi7", ">=", 0.4]]], "H": 6, "tp_atr": 1.5, "sl_atr": 1.0},
                                 "stats": {"rows": [{"group": "test", "side": "long", "trades": 10, "dir_hit": 0.6,
                                                     "win_rate": 0.55, "avg_net": 0.002, "trades_per_coin_day": 0.3}]}}))
    app = create_app(tmp / "data", model)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    while not srv.started:
        time.sleep(0.05)
    yield app, f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(10)


def test_full_flow(server):
    app, base = server
    OUT.mkdir(exist_ok=True)
    token = app.state.tsa.setup_token
    with pw.sync_playwright() as p:
        exe = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium")
        b = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"{base}/#setup={token}")
        pw.expect(page.locator("#setup-form")).to_be_visible()
        assert page.input_value("#setup-token") == token
        page.fill("#setup-pw", PW)
        page.fill("#setup-pw2", PW)
        page.click("#setup-form button[type=submit]")
        pw.expect(page.locator("#app")).to_be_visible()
        pw.expect(page.locator("#mode-badge")).to_have_text("PAPER")

        # settings -> replay mode, 3 coins, budget 300
        page.click("a[data-view=settings]")
        pw.expect(page.locator("#s-symbols")).not_to_have_value("")   # settings loaded
        page.select_option("#s-mode", "replay")
        page.fill("#s-budget", "300")
        page.fill("#s-maxpos", "3")
        page.fill("#s-speed", "0.01")
        page.fill("#s-symbols", "NEARUSDT, BTCUSDT, ETHUSDT")
        page.click("#settings-form button[type=submit]")
        pw.expect(page.locator(".toast.ok").last).to_contain_text("Ayarlar kaydedildi")
        pw.expect(page.locator("#mode-badge")).to_have_text("REPLAY")

        # invalid symbol is rejected with a message
        page.fill("#s-symbols", "NEARUSDT, ETHBTC")
        page.click("#settings-form button[type=submit]")
        pw.expect(page.locator(".toast.err").last).to_contain_text("USDT")
        page.fill("#s-symbols", "NEARUSDT, BTCUSDT, ETHUSDT")
        page.click("#settings-form button[type=submit]")

        # stored XSS attempt through the event log must render as text
        app.state.tsa.store.event("error", '<img src=x onerror="window.__xss=1">')

        page.click("a[data-view=panel]")
        page.click("#btn-start")
        pw.expect(page.locator("#run-pill")).to_have_class(re.compile("on"))
        for _ in range(120):                       # CSP forbids eval, so poll from Python
            if page.locator("#feed-mini li").count() > 3:
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(8000)
        page.screenshot(path=str(OUT / "panel.png"), full_page=True)
        assert page.evaluate("window.__xss === undefined")
        assert page.locator("#feed-mini img").count() == 0

        page.click("a[data-view=trades]")
        page.wait_for_timeout(3500)
        page.screenshot(path=str(OUT / "trades.png"), full_page=True)
        page.click("a[data-view=signals]")
        page.wait_for_timeout(3500)
        assert page.locator("#sig-table tbody tr").count() >= 1
        page.click("a[data-view=model]")
        pw.expect(page.locator("#model-long .rule")).to_have_count(1)
        page.click("a[data-view=keys]")
        pw.expect(page.locator("#keys-state")).to_have_text("yok")
        page.screenshot(path=str(OUT / "keys.png"), full_page=True)

        page.click("a[data-view=panel]")
        page.click("#btn-stop")
        pw.expect(page.locator("#run-pill")).not_to_have_class(re.compile("on"), timeout=30000)
        st = app.state.tsa.store.stats("replay")
        assert st["trades"] > 0

        # mobile layout renders without horizontal overflow
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(500)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=str(OUT / "mobile.png"), full_page=True)

        # logout -> login screen; wrong password shows an error
        page.set_viewport_size({"width": 1440, "height": 900})
        page.click("#logout")
        pw.expect(page.locator("#login-form")).to_be_visible()
        page.fill("#login-pw", "wrong-password")
        page.click("#login-form button[type=submit]")
        pw.expect(page.locator(".toast.err").last).to_contain_text("hatalı")
        page.fill("#login-pw", PW)
        page.click("#login-form button[type=submit]")
        pw.expect(page.locator("#app")).to_be_visible()
        b.close()
    assert not [e for e in errors if "Failed to load resource" not in e], errors  # 422/401 are the negative tests
