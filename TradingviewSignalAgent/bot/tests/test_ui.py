"""Browser tests (Playwright + Chromium) of every screen and button of the glass UI:
setup/login/logout, budget slider + % buttons + manual amount, leverage/stop sliders and their persistence,
mode & symbols, app preferences, start / stop (3 modes) / resume / panic, closing single and all positions,
trade history ranges and +/- colours, charts with hover, Telegram token + pairing + test + preferences,
API keys, model, signals, log, XSS safety, phone layout, no JavaScript errors."""
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

from test_telegram import TOKEN, FakeAPI, msg  # noqa: E402

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PW = "ui-test-password-123"
OUT = tsabot.BOT_ROOT / "tests" / "screenshots"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    if not (tsabot.REPO_ROOT / "data" / "raw" / "NEARUSDT_5m.parquet").exists():
        pytest.skip("research data not downloaded")
    tmp = tmp_path_factory.mktemp("ui")
    model = tmp / "model.json"
    # frequent entries that stay open for a while (far TP/SL, 40-bar time exit) so they can be closed by hand
    model.write_text(json.dumps({"name": "UI test model",
                                 "long": {"rules": [[["rsi7", "<=", -0.2]]], "H": 40, "tp_atr": 60, "sl_atr": 60},
                                 "short": {"rules": [[["rsi7", ">=", 0.25]]], "H": 40, "tp_atr": 60, "sl_atr": 60},
                                 "stats": {"rows": [{"group": "test", "side": "long", "trades": 10, "dir_hit": float("nan"),
                                                     "win_rate": 0.55, "avg_net": 0.002}]}}))
    FakeAPI.instances.clear()
    app = create_app(tmp / "data", model, {"telegram_api": FakeAPI})
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    while not srv.started:
        time.sleep(0.05)
    OUT.mkdir(exist_ok=True)
    with pw.sync_playwright() as p:
        exe = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium")
        b = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(20000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        yield app, page, f"http://127.0.0.1:{port}", errors
        b.close()
    srv.should_exit = True
    th.join(10)


def toast(page, kind, text):
    expect(page.locator(f".toast.{kind}").last).to_contain_text(text)


def go(page, view):
    page.click(f"a[data-view={view}]")
    expect(page.locator(f"#view-{view}")).to_be_visible()


def wait_for(fn, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(0.25)
    return False


# ------------------------------------------------------------------------------------------ auth
def test_setup_login_logout(ui):
    app, page, base, errors = ui
    page.goto(f"{base}/#setup={app.state.tsa.setup_token}")
    expect(page.locator("#setup-form")).to_be_visible()
    expect(page.locator("#setup-token")).to_be_hidden()               # token comes from the link itself
    page.fill("#setup-pw", PW)
    page.fill("#setup-pw2", PW + "x")
    page.click("#setup-form button[type=submit]")
    toast(page, "err", "aynı değil")
    page.fill("#setup-pw2", PW)
    page.click("#setup-form button[type=submit]")
    expect(page.locator("#app")).to_be_visible()
    expect(page.locator("#mode-badge")).to_have_text("PAPER")
    page.click("#logout")
    expect(page.locator("#login-form")).to_be_visible()
    expect(page.locator("#login-remember-row")).to_be_hidden()        # Windows only
    page.fill("#login-pw", "wrong-password")
    page.click("#login-form button[type=submit]")
    toast(page, "err", "hatalı")
    page.fill("#login-pw", PW)
    page.click("#login-form button[type=submit]")
    expect(page.locator("#app")).to_be_visible()


# ------------------------------------------------------------------------------------------ budget & risk
def test_budget_slider_percent_and_manual_amount(ui):
    app, page, base, errors = ui
    go(page, "budget")
    expect(page.locator("#s-budget")).to_have_value("100")
    expect(page.locator("#wallet-box")).to_contain_text("1.000,00 $")          # paper wallet
    page.click("#b-pcts button[data-p='25']")
    expect(page.locator("#s-budget")).to_have_value("250")
    assert page.input_value("#s-budget-range") == "250"
    expect(page.locator("#b-val")).to_have_text("250,00 $")
    page.click("#b-pcts button[data-p='100']")
    expect(page.locator("#s-budget")).to_have_value("1000")
    page.focus("#s-budget-range")                                        # slider moves the amount
    page.keyboard.press("Home")
    expect(page.locator("#s-budget")).to_have_value("5")
    for _ in range(3):
        page.keyboard.press("ArrowRight")
    expect(page.locator("#s-budget")).to_have_value("8")
    page.fill("#s-budget", "300")                                        # typed amount moves the slider
    assert page.input_value("#s-budget-range") == "300"
    page.fill("#s-budget", "5000")
    expect(page.locator("#b-warn")).to_be_visible()                      # more than the wallet
    page.fill("#s-budget", "300")
    expect(page.locator("#b-warn")).to_be_hidden()


def test_leverage_stop_and_positions_persist(ui):
    app, page, base, errors = ui
    page.focus("#s-lev-range")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    expect(page.locator("#lev-val")).to_have_text("3x")
    expect(page.locator("#lev-risk")).to_contain_text("orta risk")
    assert page.get_attribute("#s-emerg-range", "max") == "32"              # liquidation limit at 3x
    page.fill("#s-emerg", "6.5")
    expect(page.locator("#stop-val")).to_have_text("%6,5")
    page.fill("#s-maxpos", "5")
    expect(page.locator("#maxpos-val")).to_have_text("5")
    prev = page.locator("#size-preview")
    expect(prev).to_contain_text("60,00 $")                              # 300 / 5
    expect(prev).to_contain_text("180,00 $")                             # x3
    expect(prev).to_contain_text("11,70 $")                              # 180 x 6.5 %
    page.click("#btn-risk-save")
    toast(page, "ok", "Kaydedildi")
    s = app.state.tsa.settings
    assert (s.budget_usdt, s.leverage, s.emergency_stop_pct, s.max_positions) == (300, 3, 6.5, 5)
    page.reload()                                                        # values come back from the server
    expect(page.locator("#app")).to_be_visible()
    go(page, "budget")
    expect(page.locator("#s-lev")).to_have_value("3")
    expect(page.locator("#s-emerg")).to_have_value("6.5")
    expect(page.locator("#s-budget")).to_have_value("300")
    expect(page.locator("#s-maxpos")).to_have_value("5")
    page.fill("#s-lev", "10")
    page.fill("#s-emerg", "12")
    page.click("#btn-risk-save")                                        # stop beyond liquidation: the form
    assert page.evaluate("document.getElementById('s-emerg').validity.rangeOverflow")   # refuses to send it
    assert app.state.tsa.settings.leverage == 3
    page.fill("#s-lev", "1")
    page.fill("#s-emerg", "8")
    page.fill("#s-maxpos", "4")
    page.click("#btn-risk-save")
    toast(page, "ok", "Kaydedildi")
    page.screenshot(path=str(OUT / "budget.png"), full_page=True)


def test_mode_symbols_and_app_prefs(ui):
    app, page, base, errors = ui
    go(page, "settings")
    expect(page.locator("#s-symbols")).not_to_have_value("")
    page.select_option("#s-mode", "replay")
    expect(page.locator("#s-speed")).to_be_visible()
    page.fill("#s-speed", "0.3")
    page.fill("#s-symbols", "NEARUSDT, ETHBTC")
    page.click("#mode-form button[type=submit]")
    toast(page, "err", "USDT")
    page.fill("#s-symbols", "NEARUSDT, BTCUSDT, ETHUSDT, SOLUSDT")
    page.click("#mode-form button[type=submit]")
    toast(page, "ok", "Ayarlar kaydedildi")
    expect(page.locator("#mode-badge")).to_have_text("REPLAY")
    page.select_option("#s-mode", "live")
    expect(page.locator("#live-box")).to_be_visible()
    page.select_option("#s-mode", "replay")
    expect(page.locator("#p-autostart")).to_be_disabled()               # browser mode: no Windows start-up
    expect(page.locator("#p-remember")).to_be_disabled()
    expect(page.locator("#app-kind")).to_have_text("tarayıcı")
    page.click("label:has(#p-resume)")
    toast(page, "ok", "Kaydedildi")
    assert app.state.tsa.app_prefs.resume_bot is False
    page.click("label:has(#p-resume)")
    toast(page, "ok", "Kaydedildi")
    assert app.state.tsa.app_prefs.resume_bot is True
    expect(page.locator("#app-info")).to_contain_text("Veri klasörü")


# ------------------------------------------------------------------------------------------ trading
def test_start_and_close_positions_by_hand(ui):
    app, page, base, errors = ui
    st = app.state.tsa
    app.state.tsa.store.event("error", '<img src=x onerror="window.__xss=1">')     # stored XSS attempt
    go(page, "panel")
    page.click("#btn-start")
    toast(page, "ok", "Bot başlatıldı")
    expect(page.locator("#run-pill")).to_have_class(re.compile(r"\bon\b"))
    expect(page.locator("#btn-start")).to_be_disabled()
    assert wait_for(lambda: page.locator("#pos-mini .js-close").count() > 0), "no position opened"
    btn = page.locator("#pos-mini .js-close").first
    sym = btn.get_attribute("data-sym")
    btn.click()
    expect(page.locator("#modal")).to_be_visible()
    expect(page.locator("#modal-title")).to_contain_text(sym)
    page.click("#modal-actions button:has-text('Vazgeç')")
    expect(page.locator("#modal")).to_be_hidden()
    manual = lambda: [t for t in st.store.trades("replay", 5000) if t["reason"] == "Elle kapatıldı"]  # noqa: E731
    assert not manual()
    assert wait_for(lambda: page.locator("#pos-mini .js-close").count() > 0)
    btn = page.locator("#pos-mini .js-close").first
    sym = btn.get_attribute("data-sym")
    btn.click()
    page.click("#modal-ok")
    toast(page, "ok", "kapatıldı")
    assert any(t["symbol"] == sym for t in manual())
    assert st.engine.running                                            # the bot keeps running
    page.screenshot(path=str(OUT / "panel.png"), full_page=True)
    assert wait_for(lambda: page.locator("#pos-mini .js-close").count() > 0)
    n_before = len(manual())
    page.locator("#view-panel .js-close-all").click()
    expect(page.locator("#modal-body")).to_contain_text("dokunulmaz")
    page.click("#modal-ok")
    toast(page, "ok", "pozisyon kapatıldı")
    assert len(manual()) > n_before and st.engine.running
    assert page.evaluate("window.__xss === undefined")


def test_stop_modes_resume_and_panic(ui):
    app, page, base, errors = ui
    st = app.state.tsa
    page.click("#btn-stop")
    expect(page.locator("#stop-drain")).to_be_visible()
    page.click("#modal-actions button:has-text('Vazgeç')")
    assert st.engine.running
    assert wait_for(lambda: len(st.engine.positions) > 0)
    page.click("#btn-stop")
    page.click("#stop-drain")                                           # no new entries, positions managed
    toast(page, "ok", "durduruldu")
    if st.engine.running:
        expect(page.locator("#run-pill")).to_have_class(re.compile(r"\bdrain\b"))
        expect(page.locator("#btn-start-text")).to_have_text("Yeni işlemleri aç")
        assert not st.engine.entries
        page.click("#btn-start")                                        # resume new entries
        toast(page, "ok", "tekrar etkin")
        expect(page.locator("#run-pill")).to_have_class(re.compile(r"\bon\b"))
        assert st.engine.entries
    else:
        page.click("#btn-start")
        toast(page, "ok", "başlatıldı")
    page.click("#btn-stop")
    page.click("#stop-full")
    toast(page, "ok", "durduruldu")
    expect(page.locator("#run-pill")).not_to_have_class(re.compile(r"\bon\b"), timeout=30000)
    assert not st.engine.running
    page.click("#btn-start")
    toast(page, "ok", "başlatıldı")
    assert wait_for(lambda: len(st.engine.positions) > 0)
    page.click("#btn-panic")
    page.click("#modal-ok")
    toast(page, "ok", "bot durdu")
    expect(page.locator("#run-pill")).not_to_have_class(re.compile(r"\bon\b"), timeout=30000)
    assert not st.engine.positions and not st.engine.running
    assert any(t["reason"] == "Acil kapatma" for t in st.store.trades("replay", 5000))


def test_trade_history_and_colours(ui):
    app, page, base, errors = ui
    go(page, "trades")
    page.click("#tr-tabs button[data-v='all']")
    expect(page.locator("#trades-table tbody tr").first).not_to_contain_text("Bu aralıkta")
    rows = page.locator("#trades-table tbody tr")
    assert rows.count() > 3
    net = page.locator("#trades-table tbody td.num.pos, #trades-table tbody td.num.neg")
    assert net.count() > 0
    for i in range(min(net.count(), 20)):
        cell = net.nth(i)
        txt, c = cell.inner_text(), cell.get_attribute("class")
        assert (txt.startswith("+") and "pos" in c) or (txt.startswith("−") and "neg" in c), (txt, c)
    expect(page.locator("#tr-stats")).to_contain_text("Net K/Z")
    for v in ("today", "7d", "30d"):
        page.click(f"#tr-tabs button[data-v='{v}']")
        expect(page.locator(f"#tr-tabs button[data-v='{v}']")).to_have_class(re.compile("on"))
    page.screenshot(path=str(OUT / "trades.png"), full_page=True)


def test_panel_charts_and_tooltips(ui):
    app, page, base, errors = ui
    go(page, "panel")
    page.click("#eq-tabs button[data-v='all']")
    expect(page.locator("#eq-change")).not_to_have_text("")
    box = page.locator("#eq-chart").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + 80)
    expect(page.locator("#eq-tip")).to_be_visible()
    expect(page.locator("#eq-tip")).to_contain_text("$")
    page.click("#eq-mode button[data-v='pnl']")
    expect(page.locator("#eq-mode button[data-v='pnl']")).to_have_class(re.compile("on"))
    page.click("#eq-mode button[data-v='capital']")
    expect(page.locator("#sum-chips .chip")).to_have_count(4)
    k = page.locator("#k-real")
    assert re.search(r"(pos|neg)", k.get_attribute("class")) or k.inner_text().startswith("0")
    page.mouse.move(0, 0)
    expect(page.locator("#eq-tip")).to_be_hidden()


def test_positions_page(ui):
    app, page, base, errors = ui
    go(page, "positions")
    expect(page.locator("#pos-table")).to_contain_text("Açık pozisyon yok")
    expect(page.locator("#ext-table")).to_contain_text("Paper modunda")


# ------------------------------------------------------------------------------------------ telegram
def test_telegram_token_pairing_test_and_prefs(ui):
    app, page, base, errors = ui
    go(page, "telegram")
    expect(page.locator("#tg-state")).to_have_text("kapalı")
    expect(page.locator("#btn-tg-pair")).to_be_disabled()
    page.fill("#tg-token", "not-a-token")
    page.click("#btn-tg-save")
    toast(page, "err", "biçimi")
    page.fill("#tg-token", TOKEN)
    page.click("#btn-tg-save")
    toast(page, "ok", "bağlandı")
    expect(page.locator("#tg-token")).to_have_value("")
    expect(page.locator("#tg-state")).to_contain_text("tsa_test_bot", timeout=10000)
    page.click("#btn-tg-pair")
    expect(page.locator("#tg-code-box")).to_be_visible()
    code = page.inner_text("#tg-code").split()[-1]
    assert re.fullmatch(r"\d{6}", code)
    expect(page.locator("#tg-open")).to_have_attribute("href", "https://t.me/tsa_test_bot")
    FakeAPI.instances[-1].updates.append(msg(f"/eslestir {code}"))
    toast(page, "ok", "Telefon eşleşti")
    expect(page.locator("#tg-paired-text")).to_contain_text("@berk")
    expect(page.locator("#tg-pill")).to_be_visible(timeout=10000)
    page.click("#btn-tg-test")
    toast(page, "ok", "Test mesajı gönderildi")
    assert any("Test mesajı" in s["text"] for s in FakeAPI.instances[-1].sent)
    page.click("label:has(#tg-daily)")
    toast(page, "ok", "Kaydedildi")
    page.select_option("#tg-hour", "9")
    toast(page, "ok", "Kaydedildi")
    p = app.state.tsa.tg_prefs
    assert p.daily_summary and p.daily_summary_hour == 9
    page.click("label:has(#tg-control)")
    toast(page, "ok", "Kaydedildi")
    assert app.state.tsa.tg_prefs.allow_control is False
    page.screenshot(path=str(OUT / "telegram.png"), full_page=True)
    page.click("#btn-tg-unpair")
    toast(page, "ok", "kaldırıldı")
    expect(page.locator("#tg-paired-text")).to_contain_text("eşleşmedi")
    page.click("#btn-tg-delete")
    page.click("#modal-ok")
    toast(page, "ok", "Silindi")
    expect(page.locator("#tg-state")).to_have_text("kapalı")


# ------------------------------------------------------------------------------------------ keys / model / signals / log
def test_keys_model_signals_log(ui):
    app, page, base, errors = ui
    go(page, "keys")
    expect(page.locator("#keys-state")).to_have_text("yok")
    page.fill("#k-key", "A" * 20)
    page.fill("#k-secret", "B" * 20)
    page.fill("#k-pw", "wrong-password")
    page.click("#keys-form button[type=submit]")
    toast(page, "err", "hatalı")
    expect(page.locator("#k-secret")).to_have_value("")                   # never kept in the page
    page.click("#btn-check-test")
    expect(page.locator("#check-list")).to_contain_text("API anahtarı yok")
    page.click("#btn-keys-del")
    page.click("#modal-ok")
    toast(page, "ok", "Silindi")
    go(page, "model")
    expect(page.locator("#model-long .rule")).to_have_count(1)
    expect(page.locator("#model-stats")).to_contain_text("test")
    go(page, "signals")
    expect(page.locator("#sig-table tbody tr").first).to_be_visible()
    expect(page.locator("#sig-table tbody tr").first).not_to_contain_text("Bot çalışınca")
    go(page, "log")
    expect(page.locator("#feed-full li").first).to_be_visible()
    assert page.locator("#feed-full img").count() == 0
    expect(page.locator("#feed-full")).to_contain_text("<img src=x")


def test_phone_layout_and_no_js_errors(ui):
    app, page, base, errors = ui
    page.set_viewport_size({"width": 390, "height": 844})
    for v in ("panel", "budget", "trades", "telegram", "settings"):
        page.goto(f"{base}/#/{v}")
        expect(page.locator(f"#view-{v}")).to_be_visible()
        page.wait_for_timeout(400)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), v
    page.goto(f"{base}/#/panel")
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    bad = [e for e in errors if "Failed to load resource" not in e]    # 4xx/5xx are the negative tests
    assert not bad, bad
