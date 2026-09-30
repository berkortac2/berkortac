"use strict";
// TSA Bot UI - no external dependencies, every dynamic value is inserted as text (no innerHTML).
const $ = (id) => document.getElementById(id);
const S = { csrf: "", view: "panel", status: null, timer: null };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v; else if (k === "text") el.textContent = v; else el.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind, text: msg });
  $("toasts").append(t); setTimeout(() => t.remove(), 5000);
}
async function api(method, path, body) {
  const opt = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") { opt.headers["Content-Type"] = "application/json"; opt.headers["X-CSRF-Token"] = S.csrf;
    opt.body = JSON.stringify(body || {}); }
  const r = await fetch(path, opt);
  const tok = r.headers.get("X-CSRF-Token"); if (tok) S.csrf = tok;
  let data = null; try { data = await r.json(); } catch (_) { data = null; }
  if (r.status === 401 && !path.startsWith("/api/auth")) { showAuth(false); throw new Error("Oturum süresi doldu"); }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || ("HTTP " + r.status));
  return data;
}
const fmt = (x, d = 2) => (x == null || isNaN(x)) ? "–" : Number(x).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
// prices / quantities: 6 significant digits, trailing zeros removed only after the decimal point
const fpx = (x) => {
  if (x == null || !isFinite(Number(x))) return "–";
  const v = Number(x);
  let t = Math.abs(v) >= 1e5 ? v.toFixed(1) : v.toPrecision(6);
  if (t.includes("e")) t = v.toFixed(10);
  return t.includes(".") ? t.replace(/0+$/, "").replace(/\.$/, "") : t;
};
const money = (x) => (x == null) ? "–" : (x >= 0 ? "+" : "") + fmt(x) + " $";
const cls = (x) => x > 0 ? "pos" : x < 0 ? "neg" : "";
const tstr = (ms) => ms ? new Date(ms).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
const dirTag = (d) => h("span", { class: "tag " + (d > 0 ? "long" : d < 0 ? "short" : "none"), text: d > 0 ? "AL / LONG" : d < 0 ? "SAT / SHORT" : "—" });

function table(el, cols, rows, empty) {
  el.replaceChildren();
  el.append(h("thead", {}, h("tr", {}, ...cols.map((c) => h("th", { text: c[0] })))));
  const tb = h("tbody");
  if (!rows.length) tb.append(h("tr", {}, h("td", { class: "empty", colspan: String(cols.length), text: empty || "Kayıt yok" })));
  for (const r of rows) tb.append(h("tr", {}, ...cols.map((c) => { const v = c[1](r); return v instanceof Node ? h("td", {}, v) : h("td", { class: c[2] ? c[2](r) : "", text: v }); })));
  el.append(tb);
}

// ---------------------------------------------------------------- auth
async function boot() {
  const st = await api("GET", "/api/auth/state");
  if (st.logged_in) { await api("GET", "/api/auth/csrf").then((d) => (S.csrf = d.csrf)); return showApp(); }
  showAuth(st.setup_required);
}
function showAuth(setup) {
  clearInterval(S.timer); $("app").classList.add("hidden"); $("auth").classList.remove("hidden");
  $("setup-form").classList.toggle("hidden", !setup); $("login-form").classList.toggle("hidden", setup);
  const m = location.hash.match(/setup=([A-Za-z0-9_-]+)/); if (m) $("setup-token").value = m[1];
}
$("setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("setup-pw").value !== $("setup-pw2").value) return toast("Şifreler aynı değil", "err");
  try { await api("POST", "/api/auth/setup", { token: $("setup-token").value.trim(), password: $("setup-pw").value });
    history.replaceState(null, "", "#/panel"); toast("Şifre kaydedildi", "ok"); showApp(); } catch (x) { toast(x.message, "err"); }
});
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("POST", "/api/auth/login", { password: $("login-pw").value }); $("login-pw").value = ""; showApp(); }
  catch (x) { toast(x.message, "err"); }
});
$("logout").addEventListener("click", async () => { try { await api("POST", "/api/auth/logout"); } catch (_) {} showAuth(false); });

function showApp() {
  $("auth").classList.add("hidden"); $("app").classList.remove("hidden");
  route(); clearInterval(S.timer); S.timer = setInterval(refresh, 3000);
}
window.addEventListener("hashchange", route);
function route() {
  const v = (location.hash.match(/^#\/(\w+)/) || [0, "panel"])[1];
  S.view = document.getElementById("view-" + v) ? v : "panel";
  document.querySelectorAll(".view").forEach((x) => x.classList.toggle("hidden", x.id !== "view-" + S.view));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  if (S.view === "settings") loadSettings(); if (S.view === "keys") loadKeys(); if (S.view === "model") loadModel();
  refresh();
}

// ---------------------------------------------------------------- refresh
async function refresh() {
  try {
    const st = await api("GET", "/api/status"); S.status = st; renderStatus(st);
    if (S.view === "panel") { renderPositions($("pos-mini"), st.positions); renderEquity(await api("GET", "/api/equity")); renderFeed($("feed-mini"), (await api("GET", "/api/events")).slice(0, 12)); }
    if (S.view === "positions") renderPositions($("pos-table"), st.positions);
    if (S.view === "trades") renderTrades(await api("GET", "/api/trades?limit=500"));
    if (S.view === "signals") renderSignals(await api("GET", "/api/signals"));
    if (S.view === "log") renderFeed($("feed-full"), await api("GET", "/api/events"));
  } catch (x) { /* shown via toast on actions only */ }
}
function renderStatus(st) {
  const b = $("mode-badge"); b.textContent = { paper: "PAPER", testnet: "TESTNET", live: "CANLI", replay: "REPLAY" }[st.mode] || st.mode;
  b.className = "badge " + st.mode;
  $("run-pill").classList.toggle("on", st.running); $("run-text").textContent = st.status;
  $("last-bar").textContent = st.last_bar ? "son mum: " + tstr(st.last_bar) : "";
  $("btn-start").disabled = st.running; $("btn-stop").disabled = !st.running;
  $("live-pw").classList.toggle("hidden", st.mode !== "live" || st.running);
  const r = $("k-real"); r.textContent = money(st.realized); r.className = "k-val " + cls(st.realized);
  $("k-real-sub").textContent = st.budget ? "bütçeye göre " + fmt(100 * st.realized / st.budget) + " %" : "";
  const t = $("k-today"); t.textContent = money(st.realized_today); t.className = "k-val " + cls(st.realized_today);
  const u = $("k-unreal"); u.textContent = money(st.unrealized); u.className = "k-val " + cls(st.unrealized);
  $("k-open").textContent = st.positions.length + " açık pozisyon";
  const pct = st.effective_budget > 0 ? Math.min(100, 100 * st.margin_used / st.effective_budget) : 0;
  $("k-budget").textContent = fmt(pct, 0) + " %"; $("k-budget-bar").style.width = pct + "%";
  $("k-budget-sub").textContent = fmt(st.margin_used) + " / " + fmt(st.effective_budget) + " USDT marjin";
  const s = st.stats || {}; $("k-trades").textContent = (s.trades || 0) + " · " + (s.win_rate == null ? "–" : fmt(100 * s.win_rate, 1) + " %");
  $("k-fees").textContent = "ödenen komisyon " + fmt(s.fees) + " $";
}
// the stop that is really on the exchange: the tighter of the ATR stop and the emergency stop
function stopOf(p) {
  const xs = [p.sl, p.emergency].filter((x) => x != null);
  if (!xs.length) return null;
  return p.direction > 0 ? Math.max(...xs) : Math.min(...xs);
}
function renderPositions(el, ps) {
  table(el, [["Parite", (p) => p.symbol], ["Yön", (p) => dirTag(p.direction)], ["Miktar", (p) => fpx(p.qty)],
    ["Giriş", (p) => fpx(p.entry_price)], ["Son", (p) => fpx(p.last_price)], ["TP", (p) => fpx(p.tp)],
    ["Stop", (p) => fpx(stopOf(p))], ["Mum", (p) => p.bars_held + " / " + p.H], ["Marjin", (p) => fmt(p.margin)],
    ["K/Z", (p) => money(p.unrealized), (p) => cls(p.unrealized)]], ps, "Açık pozisyon yok");
}
function renderTrades(ts) {
  table($("trades-table"), [["Kapanış", (t) => tstr(t.exit_time)], ["Parite", (t) => t.symbol], ["Yön", (t) => dirTag(t.direction)],
    ["Giriş", (t) => fpx(t.entry_price)], ["Çıkış", (t) => fpx(t.exit_price)], ["Miktar", (t) => fpx(t.qty)],
    ["Neden", (t) => t.reason], ["Komisyon", (t) => fmt(t.fees)], ["Net", (t) => money(t.pnl), (t) => cls(t.pnl)]], ts, "Henüz işlem yok");
}
function renderSignals(ss) {
  table($("sig-table"), [["Parite", (s) => s.symbol], ["Sinyal", (s) => dirTag(s.direction)], ["Mum", (s) => tstr(s.bar_time)],
    ["Kapanış", (s) => fpx(s.close)], ["ATR", (s) => fpx(s.atr)],
    ["Kural özellikleri", (s) => Object.entries(s.features || {}).map(([k, v]) => k + "=" + (v == null ? "–" : Number(v).toFixed(3))).join("  ")]],
    ss, "Bot çalışınca her 5 dk kapanışında dolar");
}
function renderFeed(el, evs) {
  el.replaceChildren(...(evs.length ? evs.map((e) => h("li", { class: "lv-" + e.level }, h("span", { class: "t", text: tstr(e.ts) }), h("span", { text: e.msg })))
    : [h("li", {}, h("span", { class: "t", text: "" }), h("span", { class: "muted", text: "Olay yok" }))]));
}
function renderEquity(pts) {
  const c = $("eq-chart"), dpr = window.devicePixelRatio || 1, W = c.clientWidth || 600, H = 240;
  c.width = W * dpr; c.height = H * dpr; const g = c.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  if (pts.length < 2) { g.fillStyle = "#8b97ad"; g.font = "13px system-ui"; g.fillText("Veri bot çalıştıkça oluşur", 16, H / 2); $("eq-range").textContent = ""; return; }
  const ys = pts.map((p) => p.equity), lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.1 || 1;
  const X = (i) => 8 + (W - 16) * i / (pts.length - 1), Y = (v) => 12 + (H - 34) * (1 - (v - lo + pad) / (hi - lo + 2 * pad));
  g.strokeStyle = "rgba(139,151,173,.12)"; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) { const y = 12 + (H - 34) * i / 4; g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
  const up = ys[ys.length - 1] >= ys[0], col = up ? "34,197,94" : "240,68,82";
  const grad = g.createLinearGradient(0, 0, 0, H); grad.addColorStop(0, `rgba(${col},.28)`); grad.addColorStop(1, `rgba(${col},0)`);
  g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(i), Y(p.equity)) : g.moveTo(X(i), Y(p.equity))));
  g.lineTo(X(pts.length - 1), H - 22); g.lineTo(X(0), H - 22); g.closePath(); g.fillStyle = grad; g.fill();
  g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(i), Y(p.equity)) : g.moveTo(X(i), Y(p.equity))));
  g.strokeStyle = `rgb(${col})`; g.lineWidth = 2; g.stroke();
  g.fillStyle = "#8b97ad"; g.font = "11px system-ui"; g.fillText(tstr(pts[0].ts), 8, H - 6);
  const last = tstr(pts[pts.length - 1].ts); g.fillText(last, W - 8 - g.measureText(last).width, H - 6);
  $("eq-range").textContent = fmt(lo) + " – " + fmt(hi) + " USDT";
}

// ---------------------------------------------------------------- actions
async function act(path, okMsg, body) { try { await api("POST", path, body); if (okMsg) toast(okMsg, "ok"); refresh(); } catch (x) { toast(x.message, "err"); } }
$("btn-start").addEventListener("click", async () => {
  const live = S.status && S.status.mode === "live";
  if (live && !confirm("GERÇEK PARA ile işlem başlatılacak. Emin misin?")) return;
  await act("/api/bot/start", "Bot başlatıldı", live ? { password: $("live-pw").value } : {});
  $("live-pw").value = "";
});
$("btn-stop").addEventListener("click", () => act("/api/bot/stop", "Bot durduruldu"));
$("btn-panic").addEventListener("click", () => { if (confirm("Botun açtığı TÜM pozisyonlar piyasa fiyatından kapatılacak ve bot duracak. Emin misin?")) act("/api/bot/panic", "Pozisyonlar kapatıldı, bot durdu"); });
$("btn-reset").addEventListener("click", () => { if (confirm("Paper/replay işlem geçmişi silinsin mi?")) act("/api/bot/reset", "Geçmiş sıfırlandı"); });

async function loadSettings() {
  try {
    const s = await api("GET", "/api/settings");
    $("s-mode").value = s.mode; $("s-budget").value = s.budget_usdt; $("s-maxpos").value = s.max_positions; $("s-lev").value = s.leverage;
    $("s-daily").value = s.daily_loss_limit_pct; $("s-maxdd").value = s.max_drawdown_pct; $("s-emerg").value = s.emergency_stop_pct;
    $("s-speed").value = s.replay_speed; $("s-long").checked = s.allow_long; $("s-short").checked = s.allow_short;
    $("s-compound").checked = s.compound; $("s-symbols").value = s.symbols.join(", "); $("s-live").checked = s.live_confirmed;
    $("live-box").classList.toggle("hidden", s.mode !== "live");
  } catch (x) { toast(x.message, "err"); }
}
$("s-mode").addEventListener("change", () => $("live-box").classList.toggle("hidden", $("s-mode").value !== "live"));
$("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { mode: $("s-mode").value, budget_usdt: +$("s-budget").value, max_positions: +$("s-maxpos").value, leverage: +$("s-lev").value,
    daily_loss_limit_pct: +$("s-daily").value, max_drawdown_pct: +$("s-maxdd").value, emergency_stop_pct: +$("s-emerg").value,
    replay_speed: +$("s-speed").value, allow_long: $("s-long").checked, allow_short: $("s-short").checked, compound: $("s-compound").checked,
    symbols: $("s-symbols").value.split(/[\s,;]+/).filter(Boolean).map((x) => x.toUpperCase()), live_confirmed: $("s-live").checked };
  try { await api("PUT", "/api/settings", body); toast("Ayarlar kaydedildi", "ok"); refresh(); } catch (x) { toast(x.message, "err"); }
});

async function loadKeys() {
  try {
    const k = await api("GET", "/api/keys"); const b = $("keys-state");
    b.textContent = k.source === "env" ? "ortam değişkeni" : k.stored ? (k.unlocked ? "kayıtlı · açık" : "kayıtlı · kilitli") : "yok";
    b.className = "badge " + (k.unlocked ? "ok" : ""); renderCheck(k.check || {});
  } catch (x) { toast(x.message, "err"); }
}
function renderCheck(c) {
  const L = $("check-list"); L.replaceChildren();
  if (!c || !c.venue) { L.append(h("li", { class: "muted", text: "Henüz kontrol edilmedi" })); return; }
  const row = (k, v, good) => L.append(h("li", {}, h("span", { text: k }), h("b", { class: good ? "pos" : "neg", text: v })));
  row("Hesap", c.venue === "live" ? "Canlı" : "Testnet", true);
  if (c.error) row("Durum", c.error, false);
  if (c.available_usdt != null) row("Kullanılabilir USDT", fmt(c.available_usdt), true);
  if (c.dual_side != null) row("Pozisyon modu", c.dual_side ? "Hedge (One-way yapılmalı)" : "One-way ✓", !c.dual_side);
  if (c.futures != null) row("Futures izni", c.futures ? "açık ✓" : "kapalı", c.futures);
  if (c.withdrawals != null) row("Para çekme izni", c.withdrawals ? "AÇIK ✗" : "kapalı ✓", !c.withdrawals);
  if (c.universal_transfer != null) row("Transfer izni", (c.universal_transfer || c.internal_transfer) ? "AÇIK ✗" : "kapalı ✓", !(c.universal_transfer || c.internal_transfer));
  if (c.ip_restricted != null) row("IP kısıtlaması", c.ip_restricted ? "var ✓" : "yok (önerilir)", c.ip_restricted);
}
$("keys-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("PUT", "/api/keys", { password: $("k-pw").value, api_key: $("k-key").value.trim(), api_secret: $("k-secret").value.trim() });
    toast("Anahtar şifrelenip kaydedildi", "ok"); } catch (x) { toast(x.message, "err"); }
  $("k-key").value = ""; $("k-secret").value = ""; $("k-pw").value = ""; loadKeys();
});
$("btn-keys-del").addEventListener("click", async () => { if (!confirm("Kayıtlı API anahtarı silinsin mi?")) return; try { await api("DELETE", "/api/keys"); toast("Silindi", "ok"); loadKeys(); } catch (x) { toast(x.message, "err"); } });
for (const [id, venue] of [["btn-check-test", "testnet"], ["btn-check-live", "live"]])
  $(id).addEventListener("click", async () => { try { renderCheck(await api("POST", "/api/keys/check", { venue })); } catch (x) { toast(x.message, "err"); } });

async function loadModel() {
  try {
    const m = await api("GET", "/api/model");
    for (const [side, el] of [["long", $("model-long")], ["short", $("model-short")]]) {
      const d = m[side] || {}; el.replaceChildren();
      el.append(h("div", { class: "meta" }, h("span", { class: "badge", text: "süre " + d.H + " mum" }),
        h("span", { class: "badge", text: d.tp_atr ? "TP " + d.tp_atr + "×ATR" : "TP yok" }), h("span", { class: "badge", text: d.sl_atr ? "SL " + d.sl_atr + "×ATR" : "SL yok (acil stop)" })));
      if (!d.rules || !d.rules.length) el.append(h("div", { class: "muted", text: "Bu yönde doğrulamadan geçen kural yok." }));
      (d.exit_rules || []).forEach((r) => el.append(h("div", { class: "rule" }, h("b", { text: "ÇIK (kâr al)  " }),
        ...r.flatMap((c, j) => [j ? h("span", { class: "and", text: "  VE  " }) : null, c[0] + " " + c[1] + " " + Number(c[2]).toFixed(4)]))));
      (d.rules || []).forEach((r, i) => el.append(h("div", { class: "rule" }, h("b", { text: "#" + (i + 1) + "  " }),
        ...r.flatMap((c, j) => [j ? h("span", { class: "and", text: "  VE  " }) : null, c[0] + " " + c[1] + " " + Number(c[2]).toFixed(4)]))));
    }
    const rows = (m.stats && m.stats.rows) || [];
    table($("model-stats"), [["Grup", (r) => r.group], ["Yön", (r) => r.side], ["İşlem", (r) => r.trades], ["Yön isabeti", (r) => fmt(100 * r.dir_hit, 1) + " %"],
      ["Kazanma", (r) => fmt(100 * r.win_rate, 1) + " %"], ["Net/işlem", (r) => fmt(100 * r.avg_net, 2) + " %", (r) => cls(r.avg_net)],
      ["Coin başı günlük işlem", (r) => fmt(r.trades_per_coin_day, 2)]], rows, "İstatistik yok");
  } catch (x) { toast(x.message, "err"); }
}
boot().catch((x) => toast(x.message, "err"));
