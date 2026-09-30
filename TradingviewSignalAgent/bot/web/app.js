"use strict";
// TSA Bot UI - no external dependencies; every dynamic value is inserted as text (never innerHTML).
const $ = (id) => document.getElementById(id);
const S = { csrf: "", view: "panel", status: null, timer: null, settings: null, wallet: null, app: null, tg: null,
  eqRange: "7d", eqMode: "capital", trRange: "30d", eqData: [], dayData: [], lastCharts: 0, tgPoll: null, desk: "" };
const GREEN = "52,211,153", RED = "251,113,133";

// ---------------------------------------------------------------- dom helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v; else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids) if (k != null) el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return el;
}
function icon(name, cls = "ico sm") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("class", cls);
  const u = document.createElementNS("http://www.w3.org/2000/svg", "use"); u.setAttribute("href", "#i-" + name); s.append(u); return s;
}
function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind, text: msg });
  $("toasts").append(t); setTimeout(() => t.remove(), kind === "err" ? 7000 : 4500);
}
async function api(method, path, body) {
  const opt = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") { opt.headers["Content-Type"] = "application/json"; opt.headers["X-CSRF-Token"] = S.csrf; opt.body = JSON.stringify(body || {}); }
  const r = await fetch(path, opt);
  const tok = r.headers.get("X-CSRF-Token"); if (tok) S.csrf = tok;
  let data = null; try { data = await r.json(); } catch (_) { data = null; }
  // only a missing / expired session logs out; a wrong password (keys, live start) is a normal error message
  if (r.status === 401 && data && data.detail === "Oturum gerekli") { showAuth(false); throw new Error("Oturum süresi doldu"); }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || ("HTTP " + r.status));
  return data;
}

// ---------------------------------------------------------------- formatting
const fmt = (x, d = 2) => (x == null || isNaN(x)) ? "–" : Number(x).toLocaleString("tr-TR", { minimumFractionDigits: d, maximumFractionDigits: d });
const fpx = (x) => {                       // prices / quantities: 6 significant digits
  if (x == null || !isFinite(Number(x))) return "–";
  const v = Number(x); let t = Math.abs(v) >= 1e5 ? v.toFixed(1) : v.toPrecision(6);
  if (t.includes("e")) t = v.toFixed(10);
  return t.includes(".") ? t.replace(/0+$/, "").replace(/\.$/, "") : t;
};
const r2 = (x) => Math.round(x * 100) / 100;           // sign and colour follow the value shown (cents)
const money = (x) => { if (x == null || isNaN(x)) return "–"; const r = r2(x); return (r > 0 ? "+" : r < 0 ? "−" : "") + fmt(Math.abs(r)) + " $"; };
const usd = (x) => (x == null || isNaN(x)) ? "–" : fmt(x) + " $";
const pct = (x, d = 1) => { if (x == null || isNaN(x)) return "–"; const k = 10 ** d, r = Math.round(x * k) / k; return (r > 0 ? "+" : r < 0 ? "−" : "") + "%" + fmt(Math.abs(r), d); };
const cls = (x) => x == null || isNaN(x) ? "" : r2(x) > 0 ? "pos" : r2(x) < 0 ? "neg" : "";
const clsP = (x) => x == null || isNaN(x) ? "" : Math.round(x * 10) > 0 ? "pos" : Math.round(x * 10) < 0 ? "neg" : "";  // as pct() shows it
const tstr = (ms) => ms ? new Date(ms).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
const dstr = (ms) => new Date(ms).toLocaleDateString("tr-TR", { day: "2-digit", month: "short" });
const dur = (min) => { min = Math.max(0, Math.round(min)); return min < 60 ? min + " dk" : Math.floor(min / 60) + " sa " + (min % 60) + " dk"; };
const dirTag = (d) => h("span", { class: "tag " + (d > 0 ? "long" : d < 0 ? "short" : "none"), text: d > 0 ? "LONG" : d < 0 ? "SHORT" : "—" });
function setMoney(el, x) { el.textContent = money(x); el.classList.remove("pos", "neg"); const c = cls(x); if (c) el.classList.add(c); }

function table(el, cols, rows, empty) {
  el.replaceChildren();
  el.append(h("thead", {}, h("tr", {}, ...cols.map((c) => h("th", { class: c[3] || "", text: c[0] })))));
  const tb = h("tbody");
  if (!rows.length) tb.append(h("tr", {}, h("td", { class: "empty", colspan: String(cols.length), text: empty || "Kayıt yok" })));
  for (const r of rows) tb.append(h("tr", {}, ...cols.map((c) => {
    const v = c[1](r); const k = [(c[2] ? c[2](r) : ""), c[3] || ""].join(" ").trim();
    return v instanceof Node ? h("td", { class: k }, v) : h("td", { class: k, text: v });
  })));
  el.append(tb);
}

// ---------------------------------------------------------------- modal
function modal(title, body, actions) {
  return new Promise((resolve) => {
    const m = $("modal"); $("modal-title").textContent = title;
    const b = $("modal-body"); b.replaceChildren(...(Array.isArray(body) ? body : [body]).map((x) => x instanceof Node ? x : h("p", { text: x })));
    const a = $("modal-actions"); a.replaceChildren();
    const done = (v) => { m.classList.add("hidden"); document.removeEventListener("keydown", key); resolve(v); };
    const key = (e) => { if (e.key === "Escape") done(null); };
    for (const act of actions) a.append(h("button", { class: "btn " + (act.kind || ""), type: "button", id: act.id, onclick: () => done(act.value) }, act.label));
    b.querySelectorAll("[data-value]").forEach((x) => x.addEventListener("click", () => done(x.dataset.value)));
    m.onclick = (e) => { if (e.target === m) done(null); };
    document.addEventListener("keydown", key);
    m.classList.remove("hidden");
    const f = b.querySelector("input") || a.querySelector(".primary, .danger"); if (f) f.focus();
  });
}
const confirmBox = (title, text, ok = "Evet", kind = "danger") => modal(title, text, [{ label: "Vazgeç", kind: "ghost", value: null }, { label: ok, kind, value: "ok", id: "modal-ok" }]);

// ---------------------------------------------------------------- auth
async function boot() {
  const m = location.hash.match(/desk=([A-Za-z0-9_-]+)/); if (m) S.desk = m[1];
  const st = await api("GET", "/api/auth/state");
  $("login-remember-row").classList.toggle("hidden", !st.remember_available);
  if (st.logged_in) { const d = await api("GET", "/api/auth/csrf"); S.csrf = d.csrf; return showApp(); }
  if (!st.setup_required && S.desk) {
    try { await api("POST", "/api/auth/desktop", { token: S.desk }); history.replaceState(null, "", "#/panel"); return showApp(); } catch (_) { /* login needed */ }
  }
  showAuth(st.setup_required);
}
function showAuth(setup) {
  clearInterval(S.timer); $("app").classList.add("hidden"); $("auth").classList.remove("hidden");
  $("setup-form").classList.toggle("hidden", !setup); $("login-form").classList.toggle("hidden", setup);
  const m = location.hash.match(/setup=([A-Za-z0-9_-]+)/);
  if (m) { $("setup-token").value = m[1]; document.querySelector(".hidden-in-desktop").classList.add("hidden"); }
  (setup ? $("setup-pw") : $("login-pw")).focus();
}
$("setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("setup-pw").value !== $("setup-pw2").value) return toast("Şifreler aynı değil", "err");
  try { await api("POST", "/api/auth/setup", { token: $("setup-token").value.trim(), password: $("setup-pw").value });
    history.replaceState(null, "", "#/panel"); toast("Şifre kaydedildi", "ok"); showApp(); } catch (x) { toast(x.message, "err"); }
});
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("POST", "/api/auth/login", { password: $("login-pw").value, remember: $("login-remember").checked });
    $("login-pw").value = ""; if (/desk=|setup=/.test(location.hash)) history.replaceState(null, "", "#/panel"); showApp(); }
  catch (x) { toast(x.message, "err"); }
});
$("logout").addEventListener("click", async () => { try { await api("POST", "/api/auth/logout"); } catch (_) {} showAuth(false); });

function showApp() {
  $("auth").classList.add("hidden"); $("app").classList.remove("hidden");
  loadApp(); route(); clearInterval(S.timer); S.timer = setInterval(refresh, 3000);
}
window.addEventListener("hashchange", route);
function route() {
  const v = (location.hash.match(/^#\/(\w+)/) || [0, "panel"])[1];
  S.view = document.getElementById("view-" + v) ? v : "panel";
  document.querySelectorAll(".view").forEach((x) => x.classList.toggle("hidden", x.id !== "view-" + S.view));
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === S.view));
  clearInterval(S.tgPoll);
  if (S.view === "budget") { loadSettings(); loadWallet(false); }
  if (S.view === "settings") { loadSettings(); loadApp(); }
  if (S.view === "positions") loadWallet(false);
  if (S.view === "keys") loadKeys();
  if (S.view === "model") loadModel();
  if (S.view === "telegram") loadTelegram();
  S.lastCharts = 0; refresh();
}

// ---------------------------------------------------------------- refresh
async function refresh() {
  try {
    const st = await api("GET", "/api/status"); S.status = st; renderStatus(st);
    if (S.view === "panel") {
      renderPositions($("pos-mini"), st.positions, true);
      renderFeed($("feed-mini"), (await api("GET", "/api/events")).slice(0, 10));
      const nt = (st.stats || {}).trades;               // a trade closed: charts at once, else every 15 s
      if (Date.now() - S.lastCharts > 15000 || nt !== S.lastTrades) { S.lastCharts = Date.now(); S.lastTrades = nt; await loadCharts(); }
    }
    if (S.view === "positions") { renderPositions($("pos-table"), st.positions, false); if (!S.wallet || Date.now() - S.wallet.ts > 20000) loadWallet(false); }
    if (S.view === "trades") await loadTrades();
    if (S.view === "signals") renderSignals(await api("GET", "/api/signals"));
    if (S.view === "log") renderFeed($("feed-full"), await api("GET", "/api/events"));
    if (S.view === "telegram") renderTelegram(await api("GET", "/api/telegram"));
    if (S.view === "budget") renderPreview();
  } catch (x) { /* shown via toast on actions only */ }
}
function renderStatus(st) {
  const b = $("mode-badge"); b.textContent = { paper: "PAPER", testnet: "TESTNET", live: "CANLI", replay: "REPLAY" }[st.mode] || st.mode;
  b.className = "badge " + st.mode;
  const pill = $("run-pill"); pill.classList.toggle("on", st.state === "running" || st.state === "starting"); pill.classList.toggle("drain", st.state === "draining");
  $("run-text").textContent = st.status;
  const n = st.net || {}; const np = $("net-pill"); np.classList.remove("good", "bad", "warn");
  if (!st.running) { $("net-text").textContent = "Bağlantı –"; }
  else if (n.down_since || n.net_errors > 0 || n.blocked_s > 0) { np.classList.add("bad"); $("net-text").textContent = n.blocked_s > 0 ? "İstek limiti: bekleniyor" : "Bağlantı sorunu, tekrar deneniyor"; }
  else { const u = n.weight_limit ? Math.round(100 * n.weight_used / n.weight_limit) : 0; np.classList.add(u > 70 ? "warn" : "good"); $("net-text").textContent = "Bağlantı iyi" + (n.weight_limit ? " · limit %" + u : ""); }
  const tg = st.telegram || {}; $("tg-pill").classList.toggle("hidden", !tg.paired);
  $("tg-text").textContent = tg.status === "ok" ? "Telegram bağlı" : "Telegram: " + (tg.status || "–");
  const running = st.state === "running" || st.state === "starting";
  $("btn-start").disabled = running; $("btn-start-text").textContent = st.state === "draining" ? "Yeni işlemleri aç" : "Başlat";
  $("btn-stop").disabled = st.state === "stopped";
  // KPIs
  const unreal = st.unrealized || 0, period = st.realized_period || 0;
  $("k-capital").textContent = usd(st.budget + period + unreal);
  setMoney($("k-capital-chg"), period + unreal);
  $("k-capital-base").textContent = st.budget ? "(" + pct(100 * (period + unreal) / st.budget) + ") · bütçe " + usd(st.budget) : "";
  setMoney($("k-today"), st.realized_today); $("k-today-sub").textContent = "kapanan işlemler · açıklar dahil " + money((st.realized_today || 0) + unreal);
  setMoney($("k-unreal"), unreal); $("k-open").textContent = st.positions.length + " pozisyon · marjin " + usd(st.margin_used);
  setMoney($("k-real"), st.realized);
  const s = st.stats || {}; $("k-fees").textContent = (s.trades || 0) + " işlem · kazanma " + (s.win_rate == null ? "–" : "%" + fmt(100 * s.win_rate, 0)) + " · komisyon " + usd(s.fees);
  const p = st.effective_budget > 0 ? Math.min(100, 100 * st.margin_used / st.effective_budget) : 0;
  $("k-budget").textContent = "%" + fmt(p, 0); $("k-budget-bar").style.width = p + "%";
  $("k-budget-sub").textContent = usd(st.margin_used) + " / " + usd(st.effective_budget) + " · " + st.leverage + "x";
  $("pos-note").textContent = st.price_ts ? "fiyatlar " + new Date(st.price_ts).toLocaleTimeString("tr-TR") : "";
  document.querySelectorAll(".js-close-all").forEach((x) => (x.disabled = !st.positions.length));
}
function stopOf(p) {                         // the stop that is really on the exchange
  const xs = [p.sl, p.emergency].filter((x) => x != null); if (!xs.length) return null;
  return p.direction > 0 ? Math.max(...xs) : Math.min(...xs);
}
function renderPositions(el, ps, compact) {
  const now = (S.status && S.status.now) || Date.now();          // engine clock (replay time in replay mode)
  const cols = [["Parite", (p) => h("span", { class: "sym", text: p.symbol })], ["Yön", (p) => dirTag(p.direction)],
    ["Giriş", (p) => fpx(p.entry_price), null, "num"], ["Son", (p) => fpx(p.last_price), null, "num"],
    ["K/Z", (p) => money(p.unrealized), (p) => cls(p.unrealized), "num"], ["K/Z %", (p) => pct(p.roe_pct), (p) => clsP(p.roe_pct), "num"],
    ["Stop (borsada)", (p) => fpx(stopOf(p)) + (p.protected === false ? " ⚠" : ""), null, "num"]];
  if (!compact) cols.push(["TP", (p) => fpx(p.tp), null, "num"], ["Miktar", (p) => fpx(p.qty), null, "num"], ["Marjin", (p) => usd(p.margin), null, "num"]);
  cols.push(["Süre", (p) => dur((now - p.entry_time_ms) / 60000) + " · " + p.bars_held + "/" + p.H + " mum"],
    ["", (p) => h("button", { class: "btn xs danger-ghost js-close", "data-sym": p.symbol, onclick: () => closeOne(p) }, icon("x"), "Kapat")]);
  table(el, cols, ps, "Açık pozisyon yok");
}
async function closeOne(p) {
  const ok = await confirmBox(p.symbol + " kapatılsın mı?", [
    "Botun " + p.symbol + " " + (p.direction > 0 ? "LONG" : "SHORT") + " pozisyonu piyasa fiyatından kapatılacak.",
    "Şu anki K/Z: " + money(p.unrealized) + ". Bot çalışmaya devam eder."], "Pozisyonu kapat");
  if (!ok) return;
  try { await api("POST", "/api/positions/close", { symbol: p.symbol }); toast(p.symbol + " kapatıldı", "ok"); S.lastCharts = 0; refresh(); }
  catch (x) { toast(x.message, "err"); }
}
async function closeAll() {
  const n = S.status ? S.status.positions.length : 0; if (!n) return;
  const ok = await confirmBox("Tüm pozisyonlar kapatılsın mı?", ["Botun açtığı " + n + " pozisyonun hepsi piyasa fiyatından kapatılacak (toplam K/Z şu an " + money(S.status.unrealized) + ").",
    "Senin elle açtığın pozisyonlara dokunulmaz. Bot çalışmaya devam eder."], "Hepsini kapat");
  if (!ok) return;
  try { const r = await api("POST", "/api/positions/close-all"); toast(r.closed + " pozisyon kapatıldı", r.ok ? "ok" : "err"); if (!r.ok) toast("Bazıları kapatılamadı: " + Object.entries(r.result).filter(([, v]) => v !== "ok").map(([k, v]) => k + ": " + v).join("; "), "err"); S.lastCharts = 0; refresh(); }
  catch (x) { toast(x.message, "err"); }
}
document.querySelectorAll(".js-close-all").forEach((b) => b.addEventListener("click", closeAll));

function renderFeed(el, evs) {
  el.replaceChildren(...(evs.length ? evs.map((e) => h("li", { class: "lv-" + e.level }, h("span", { class: "t", text: tstr(e.ts) }), h("span", { text: e.msg })))
    : [h("li", {}, h("span", { class: "t", text: "" }), h("span", { class: "muted", text: "Olay yok" }))]));
}
function renderSignals(ss) {
  table($("sig-table"), [["Parite", (s) => h("span", { class: "sym", text: s.symbol })], ["Sinyal", (s) => dirTag(s.direction)], ["Mum", (s) => tstr(s.bar_time)],
    ["Kapanış", (s) => fpx(s.close), null, "num"], ["ATR", (s) => fpx(s.atr), null, "num"],
    ["Kural özellikleri", (s) => Object.entries(s.features || {}).map(([k, v]) => k + "=" + (v == null ? "–" : Number(v).toFixed(3))).join("  ")]],
  ss, "Bot çalışınca her 5 dk kapanışında dolar");
}

// ---------------------------------------------------------------- charts
function setupCanvas(c, H) {
  const dpr = window.devicePixelRatio || 1, W = c.clientWidth || 600;
  c.width = W * dpr; c.height = H * dpr; const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  return { g, W, H };
}
function eqPoints() {
  const b = S.status ? S.status.budget : 0;
  return S.eqData.map((p) => ({ t: p.ts, v: S.eqMode === "capital" ? p.equity : (p.pnl != null ? p.pnl : p.equity - (p.budget != null ? p.budget : b)) }));
}
function drawEquity(hover) {
  const c = $("eq-chart"), { g, W, H } = setupCanvas(c, 260), pts = eqPoints(), tip = $("eq-tip");
  if (pts.length < 2) { g.fillStyle = "#93a0b8"; g.font = "13px system-ui"; g.fillText("Veri bot çalıştıkça oluşur (her 5 dk bir nokta)", 16, H / 2); $("eq-change").textContent = ""; $("eq-range").textContent = ""; tip.classList.add("hidden"); return; }
  const ys = pts.map((p) => p.v), base = ys[0], lo = Math.min(...ys, base), hi = Math.max(...ys, base), pad = (hi - lo) * 0.12 || Math.max(1, Math.abs(base) * 0.01);
  const L = 8, R = 64, T = 12, B = 26, t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = (t) => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0), Y = (v) => T + (H - T - B) * (1 - (v - lo + pad) / (hi - lo + 2 * pad));
  g.font = "11px system-ui"; g.fillStyle = "#6b7890"; g.strokeStyle = "rgba(255,255,255,.06)"; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) { const v = lo - pad + (hi - lo + 2 * pad) * (1 - i / 4), y = Y(v); g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke(); g.fillText(fmt(v, Math.abs(v) >= 1000 ? 0 : 2), W - R + 6, y + 4); }
  const yb = Y(base); g.setLineDash([4, 4]); g.strokeStyle = "rgba(255,255,255,.22)"; g.beginPath(); g.moveTo(L, yb); g.lineTo(W - R, yb); g.stroke(); g.setLineDash([]);
  const path = () => { g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(X(p.t), Y(p.v)) : g.moveTo(X(p.t), Y(p.v)))); };
  for (const [col, y0, y1] of [[GREEN, 0, yb], [RED, yb, H]]) {       // green above the start value, red below
    g.save(); g.beginPath(); g.rect(0, y0, W, y1 - y0); g.clip();
    const gr = g.createLinearGradient(0, y0 === 0 ? T : H - B, 0, yb); gr.addColorStop(0, `rgba(${col},.30)`); gr.addColorStop(1, `rgba(${col},.02)`);
    path(); g.lineTo(X(t1), yb); g.lineTo(X(t0), yb); g.closePath(); g.fillStyle = gr; g.fill();
    path(); g.strokeStyle = `rgb(${col})`; g.lineWidth = 2.2; g.stroke(); g.restore();
  }
  g.fillStyle = "#6b7890"; g.fillText(tstr(t0), L, H - 7); const lt = tstr(t1); g.fillText(lt, W - R - g.measureText(lt).width, H - 7);
  const chg = ys[ys.length - 1] - base; setMoney($("eq-change"), chg);
  $("eq-range").textContent = (S.eqMode === "capital" && base ? "(" + pct(100 * chg / base, 2) + ") · " : "") + "aralık " + fmt(Math.min(...ys)) + " – " + fmt(Math.max(...ys)) + " $";
  if (hover == null) { tip.classList.add("hidden"); return; }
  let k = 0, best = 1e18; pts.forEach((p, i) => { const d = Math.abs(X(p.t) - hover); if (d < best) { best = d; k = i; } });
  const p = pts[k], x = X(p.t), y = Y(p.v), up = p.v >= base;
  g.strokeStyle = "rgba(255,255,255,.35)"; g.beginPath(); g.moveTo(x, T); g.lineTo(x, H - B); g.stroke();
  g.fillStyle = `rgb(${up ? GREEN : RED})`; g.beginPath(); g.arc(x, y, 4.5, 0, 7); g.fill(); g.strokeStyle = "#0b1020"; g.lineWidth = 2; g.stroke();
  tip.replaceChildren(h("div", { class: "muted", text: tstr(p.t) }), h("b", { text: (S.eqMode === "capital" ? usd(p.v) : money(p.v)) }), h("div", { class: cls(p.v - base), text: money(p.v - base) + " (başlangıca göre)" }));
  tip.style.left = Math.min(Math.max(x, 90), W - 90) + "px"; tip.style.top = y + "px"; tip.classList.remove("hidden");
}
function drawDaily(hover) {
  const c = $("day-chart"), { g, W, H } = setupCanvas(c, 170), d = S.dayData, tip = $("day-tip");
  if (!d.length) { g.fillStyle = "#93a0b8"; g.font = "13px system-ui"; g.fillText("Henüz kapanan işlem yok", 16, H / 2); tip.classList.add("hidden"); return; }
  const days = 30, start = d[d.length - 1].day - (days - 1) * 86400000, map = new Map(d.map((x) => [Math.round((x.day - start) / 86400000), x]));
  const vals = d.map((x) => x.pnl), m = Math.max(1e-9, ...vals.map(Math.abs)), T = 10, B = 22, mid = T + (H - T - B) / 2, bw = (W - 16) / days;
  g.strokeStyle = "rgba(255,255,255,.12)"; g.beginPath(); g.moveTo(8, mid); g.lineTo(W - 8, mid); g.stroke();
  let hk = null;
  for (let i = 0; i < days; i++) {
    const x = 8 + i * bw, it = map.get(i); if (hover != null && hover >= x && hover < x + bw) hk = i;
    if (!it) continue; const hgt = (H - T - B) / 2 * Math.abs(it.pnl) / m, up = it.pnl >= 0;
    g.fillStyle = `rgba(${up ? GREEN : RED},${hk === i ? 1 : .8})`; const y = up ? mid - hgt : mid;
    g.beginPath(); g.roundRect ? g.roundRect(x + bw * .15, y, bw * .7, Math.max(1.5, hgt), 3) : g.rect(x + bw * .15, y, bw * .7, Math.max(1.5, hgt)); g.fill();
  }
  g.fillStyle = "#6b7890"; g.font = "11px system-ui"; g.fillText(dstr(start), 8, H - 6); const e = dstr(start + (days - 1) * 86400000); g.fillText(e, W - 8 - g.measureText(e).width, H - 6);
  const it = hk != null ? map.get(hk) : null;
  if (!it) { tip.classList.add("hidden"); return; }
  tip.replaceChildren(h("div", { class: "muted", text: dstr(it.day) + " · " + it.trades + " işlem" }), h("b", { class: cls(it.pnl), text: money(it.pnl) }));
  tip.style.left = Math.min(Math.max(8 + hk * bw + bw / 2, 70), W - 70) + "px"; tip.style.top = mid + "px"; tip.classList.remove("hidden");
}
async function loadCharts() {
  const [eq, day, sum] = await Promise.all([api("GET", "/api/equity?range=" + S.eqRange), api("GET", "/api/pnl/daily?days=30"), api("GET", "/api/trades/summary")]);
  S.eqData = eq; S.dayData = day; drawEquity(); drawDaily();
  const lab = { today: "Bugün", "7d": "7 gün", "30d": "30 gün", all: "Tümü" };
  $("sum-chips").replaceChildren(...Object.entries(sum).map(([k, v]) => h("span", { class: "chip" }, h("span", { class: "muted", text: lab[k] }), h("b", { class: cls(v.net_pnl), text: money(v.net_pnl) }))));
}
function seg(id, cb) {
  $(id).addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return;
    $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); cb(b.dataset.v); });
}
seg("eq-tabs", (v) => { S.eqRange = v; S.lastCharts = 0; refresh(); });
seg("eq-mode", (v) => { S.eqMode = v; drawEquity(); });
seg("tr-tabs", (v) => { S.trRange = v; loadTrades(); });
const hov = (id, fn) => { const c = $(id); c.addEventListener("mousemove", (e) => fn(e.offsetX)); c.addEventListener("mouseleave", () => fn(null)); };
hov("eq-chart", drawEquity); hov("day-chart", drawDaily);
window.addEventListener("resize", () => { if (S.view === "panel") { drawEquity(); drawDaily(); } });

// ---------------------------------------------------------------- trades
async function loadTrades() {
  const [ts, sum] = await Promise.all([api("GET", "/api/trades?limit=1000&range=" + S.trRange), api("GET", "/api/trades/summary")]);
  const s = sum[S.trRange] || sum.all;
  const stat = (l, v, c) => h("div", { class: "stat" }, h("div", { class: "l", text: l }), h("div", { class: "v " + (c || ""), text: v }));
  $("tr-stats").replaceChildren(stat("Net K/Z", money(s.net_pnl), cls(s.net_pnl)), stat("İşlem", String(s.trades)),
    stat("Kazanma", s.win_rate == null ? "–" : "%" + fmt(100 * s.win_rate, 0)), stat("En iyi", money(s.best), cls(s.best)),
    stat("En kötü", money(s.worst), cls(s.worst)), stat("Ödenen komisyon", usd(s.fees)));
  table($("trades-table"), [["Kapanış", (t) => tstr(t.exit_time)], ["Parite", (t) => h("span", { class: "sym", text: t.symbol })], ["Yön", (t) => dirTag(t.direction)],
    ["Giriş", (t) => fpx(t.entry_price), null, "num"], ["Çıkış", (t) => fpx(t.exit_price), null, "num"], ["Miktar", (t) => fpx(t.qty), null, "num"],
    ["Süre", (t) => dur((t.exit_time - t.entry_time) / 60000)], ["Neden", (t) => t.reason], ["Komisyon", (t) => usd(t.fees), null, "num"],
    ["Net", (t) => money(t.pnl), (t) => cls(t.pnl), "num"], ["Net %", (t) => pct(t.margin ? 100 * t.pnl / t.margin : null), (t) => clsP(t.margin ? 100 * t.pnl / t.margin : null), "num"]],
  ts, "Bu aralıkta kapanan işlem yok");
}

// ---------------------------------------------------------------- bot control
$("btn-start").addEventListener("click", async () => {
  const st = S.status || {}; const live = st.mode === "live" && st.state === "stopped";
  let body = {};
  if (live) {
    const pw = h("input", { type: "password", id: "modal-pw", autocomplete: "current-password", placeholder: "Uygulama şifren" });
    const r = await modal("Canlı işlem başlatılsın mı?", [h("p", { text: "Bot GERÇEK PARA ile işlem açacak. Onaylamak için uygulama şifreni gir." }), pw],
      [{ label: "Vazgeç", kind: "ghost", value: null }, { label: "Canlı başlat", kind: "danger", value: "ok", id: "modal-ok" }]);
    if (!r) return; body = { password: pw.value };
  }
  try { await api("POST", "/api/bot/start", body); toast(st.state === "draining" ? "Yeni işlem açma tekrar etkin" : "Bot başlatıldı", "ok"); refresh(); } catch (x) { toast(x.message, "err"); }
});
$("btn-stop").addEventListener("click", async () => {
  const n = S.status ? S.status.positions.length : 0;
  const opt = (v, t, d, k) => h("button", { type: "button", class: "opt " + (k || ""), "data-value": v, id: "stop-" + v }, h("b", { text: t }), h("span", { text: d }));
  const how = await modal("Bot nasıl durdurulsun?", [
    opt("drain", "Yeni işlem açma (önerilen)", n ? "Açık " + n + " pozisyon kurallarına göre (ÇIK sinyali, süre, stop) kapanmaya devam eder; hepsi kapanınca bot tamamen durur." : "Açık pozisyon yok; bot hemen durur."),
    opt("full", "Tamamen durdur", "Bot hemen durur. Açık pozisyonlar sadece Binance'teki stop emriyle korunur; kâr al / süre çıkışı yapılmaz."),
    opt("close", "Tüm pozisyonları kapat ve durdur", "Botun bütün pozisyonları piyasa fiyatından hemen kapatılır.", "danger")],
  [{ label: "Vazgeç", kind: "ghost", value: null }]);
  if (!how) return;
  try { const r = await api("POST", "/api/bot/stop", { how }); toast(how === "close" ? r.closed + " pozisyon kapatıldı, bot durdu" : r.state === "draining" ? "Yeni işlem açma durduruldu" : "Bot durduruldu", "ok"); refresh(); }
  catch (x) { toast(x.message, "err"); }
});
$("btn-panic").addEventListener("click", async () => {
  if (!await confirmBox("Acil kapat", "Botun açtığı TÜM pozisyonlar piyasa fiyatından kapatılacak ve bot duracak. Emin misin?", "Hepsini kapat ve durdur")) return;
  try { const r = await api("POST", "/api/bot/panic"); toast(r.closed + " pozisyon kapatıldı, bot durdu", "ok"); refresh(); } catch (x) { toast(x.message, "err"); }
});

// ---------------------------------------------------------------- budget & risk
const RISK = ["s-budget", "s-lev", "s-emerg", "s-maxpos"];
function fill(r) { const p = 100 * (r.value - r.min) / ((r.max - r.min) || 1); r.style.setProperty("--fill", Math.max(0, Math.min(100, p)) + "%"); }
function link(num, rng, after) {
  const n = $(num), r = $(rng);
  n.addEventListener("input", () => { if (n.value !== "") { r.value = n.value; fill(r); } after(); });
  r.addEventListener("input", () => { n.value = r.value; fill(r); after(); });
}
function stopMax() { const l = +$("s-lev").value || 1; return Math.min(50, Math.floor((100 / l - 1 - 0.01) * 2) / 2); }
function riskUI() {
  const lev = +$("s-lev").value || 1, sm = stopMax();
  $("lev-val").textContent = lev + "x";
  const lr = $("lev-risk"); lr.className = "risk " + (lev <= 2 ? "low" : lev <= 4 ? "mid" : "high");
  lr.textContent = lev <= 2 ? "düşük risk (önerilen 1x)" : lev <= 4 ? "orta risk" : "yüksek risk: 3x'te geçmişte %69 düşüş görüldü";
  $("s-emerg-range").max = sm; $("s-emerg").max = sm; fill($("s-emerg-range"));
  $("stop-val").textContent = "%" + fmt(+$("s-emerg").value || 0, 1);
  $("maxpos-val").textContent = $("s-maxpos").value; $("b-val").textContent = usd(+$("s-budget").value);
  renderPreview(); budgetWarn();
}
link("s-budget", "s-budget-range", riskUI); link("s-lev", "s-lev-range", riskUI); link("s-emerg", "s-emerg-range", riskUI); link("s-maxpos", "s-maxpos-range", riskUI);
function renderPreview() {
  const b = +$("s-budget").value || 0, n = +$("s-maxpos").value || 1, l = +$("s-lev").value || 1, sp = +$("s-emerg").value || 0;
  if (!b) return;
  const m = b / n, size = m * l, it = (lab, v, c) => h("div", {}, h("div", { class: "l", text: lab }), h("div", { class: "v " + (c || ""), text: v }));
  $("size-preview").replaceChildren(it("Pozisyon başı marjin", usd(m)), it("Pozisyon büyüklüğü", usd(size)), it("Stop olursa en fazla", "−" + usd(size * sp / 100), "neg"));
}
function budgetWarn() {
  const w = S.wallet, b = +$("s-budget").value || 0, el = $("b-warn");
  if (w && !w.error && w.allocatable != null && b > w.allocatable + 0.01) {
    el.textContent = "Bu tutar şu an bota ayrılabilecek bakiyeden (" + usd(w.allocatable) + ") fazla. Bakiye yetmediğinde bot yeni işlem açmaz; fazlasını asla kullanmaz."; el.classList.remove("hidden");
  } else el.classList.add("hidden");
}
function budgetScale() {
  const w = S.wallet, r = $("s-budget-range"), b = +$("s-budget").value || 0;
  const base = w && !w.error && w.wallet > 0 ? w.wallet : 0;
  r.max = String(Math.max(100, Math.ceil(Math.max(base, b)))); r.step = +r.max > 5000 ? "10" : "1"; r.value = b; fill(r);
  $("b-hint").textContent = base ? "Yüzdeler futures USDT bakiyene göre: " + usd(base) + (w.source === "paper" || w.source === "replay" ? " (sanal bakiye)" : "") : "Yüzde seçenekleri için cüzdan bilgisi gerekli.";
  document.querySelectorAll("#b-pcts button").forEach((x) => (x.disabled = !base));
}
$("b-pcts").addEventListener("click", (e) => {
  const x = e.target.closest("button"); if (!x || !S.wallet || !(S.wallet.wallet > 0)) return;
  const v = Math.max(5, Math.floor(S.wallet.wallet * (+x.dataset.p) / 100));
  $("s-budget").value = v; budgetScale(); riskUI();
});
async function loadSettings() {
  try {
    const s = await api("GET", "/api/settings"); S.settings = s;
    const set = (id, v) => { $(id).value = v; const r = $(id + "-range"); if (r) { r.value = v; fill(r); } };
    set("s-budget", s.budget_usdt); set("s-lev", s.leverage); set("s-emerg", s.emergency_stop_pct); set("s-maxpos", s.max_positions);
    $("s-daily").value = s.daily_loss_limit_pct; $("s-maxdd").value = s.max_drawdown_pct;
    $("s-long").checked = s.allow_long; $("s-short").checked = s.allow_short; $("s-compound").checked = s.compound;
    $("s-mode").value = s.mode; $("s-speed").value = s.replay_speed; $("s-paperwallet").value = s.paper_wallet_usdt;
    $("s-symbols").value = s.symbols.join(", "); $("s-live").checked = s.live_confirmed;
    modeUI(); budgetScale(); riskUI();
  } catch (x) { toast(x.message, "err"); }
}
async function loadWallet(force) {
  try {
    const w = await api("GET", "/api/wallet" + (force ? "?force=1" : "")); w.ts = Date.now(); S.wallet = w; renderWallet(w);
    if (S.view === "budget") { budgetScale(); budgetWarn(); }
  } catch (x) { if (force) toast(x.message, "err"); }
}
function renderWallet(w) {
  const box = $("wallet-box"), stat = (l, v, c) => h("div", { class: "stat" }, h("div", { class: "l", text: l }), h("div", { class: "v " + (c || ""), text: v }));
  if (w.error) { box.replaceChildren(h("div", { class: "warn", text: w.error })); }
  else box.replaceChildren(stat(w.source === "paper" || w.source === "replay" ? "Sanal futures bakiyesi" : "Futures USDT bakiyesi", usd(w.wallet)),
    stat("Kullanılabilir", usd(w.available)), stat("Botun kullandığı marjin", usd(w.bot_margin)),
    stat("Senin pozisyonlarının marjini", usd(w.user_margin)), stat("Açık K/Z (hepsi)", money(w.unrealized), cls(w.unrealized)),
    stat("Bota ayrılabilir", usd(w.allocatable)));
  const ext = (w.positions || []).filter((p) => p.owner === "user");
  table($("ext-table"), [["Parite", (p) => h("span", { class: "sym", text: p.symbol })], ["Yön", (p) => dirTag(p.amount)], ["Miktar", (p) => fpx(Math.abs(p.amount)), null, "num"],
    ["Giriş", (p) => fpx(p.entry_price), null, "num"], ["Mark", (p) => fpx(p.mark_price), null, "num"], ["K/Z", (p) => money(p.unrealized), (p) => cls(p.unrealized), "num"],
    ["Marjin", (p) => usd(p.margin), null, "num"], ["", () => h("span", { class: "tag user", text: "SENİN · bot dokunmaz" })]],
  ext, w.error ? w.error : (w.source === "paper" || w.source === "replay") ? "Paper modunda gerçek hesap okunmaz" : "Elle açılmış pozisyon yok");
}
$("btn-wallet-refresh").addEventListener("click", () => loadWallet(true));
$("btn-wallet-refresh-2").addEventListener("click", () => loadWallet(true));
$("risk-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { budget_usdt: +$("s-budget").value, leverage: +$("s-lev").value, emergency_stop_pct: +$("s-emerg").value, max_positions: +$("s-maxpos").value,
    daily_loss_limit_pct: +$("s-daily").value, max_drawdown_pct: +$("s-maxdd").value, allow_long: $("s-long").checked, allow_short: $("s-short").checked,
    compound: $("s-compound").checked, live_confirmed: !!(S.settings && S.settings.live_confirmed) };
  try { S.settings = await api("PUT", "/api/settings", body); toast(S.status && S.status.running ? "Kaydedildi: yeni işlemlerde geçerli" : "Kaydedildi", "ok"); loadSettings(); refresh(); }
  catch (x) { toast(x.message, "err"); }
});

// ---------------------------------------------------------------- mode & app
function modeUI() {
  const m = $("s-mode").value; $("live-box").classList.toggle("hidden", m !== "live"); $("speed-row").classList.toggle("hidden", m !== "replay");
}
$("s-mode").addEventListener("change", modeUI);
$("mode-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = { mode: $("s-mode").value, replay_speed: +$("s-speed").value, paper_wallet_usdt: +$("s-paperwallet").value,
    symbols: $("s-symbols").value.split(/[\s,;]+/).filter(Boolean).map((x) => x.toUpperCase()), live_confirmed: $("s-live").checked };
  try { S.settings = await api("PUT", "/api/settings", body); toast("Ayarlar kaydedildi", "ok"); refresh(); } catch (x) { toast(x.message, "err"); }
});
$("btn-reset").addEventListener("click", async () => {
  if (!await confirmBox("Geçmiş silinsin mi?", "Paper/replay işlem geçmişi ve sermaye eğrisi silinecek.", "Sil")) return;
  try { await api("POST", "/api/bot/reset"); toast("Geçmiş sıfırlandı", "ok"); S.lastCharts = 0; refresh(); } catch (x) { toast(x.message, "err"); }
});
async function loadApp() {
  try {
    const a = await api("GET", "/api/app"); S.app = a;
    $("app-kind").textContent = a.desktop ? "masaüstü" : "tarayıcı"; $("app-kind").className = "badge " + (a.desktop ? "ok" : "");
    $("p-autostart").checked = a.prefs.autostart; $("p-autostart").disabled = !(a.desktop && a.platform === "win32");
    $("p-resume").checked = a.prefs.resume_bot; $("p-notify").checked = a.prefs.notify_desktop; $("p-notify").disabled = !a.desktop;
    $("p-awake").checked = a.prefs.prevent_sleep; $("p-awake").disabled = !(a.desktop && a.platform === "win32");
    $("p-remember").checked = a.remembered; $("p-remember").disabled = !a.remember_available;
    const opt = [...$("s-mode").options].find((o) => o.value === "replay"); if (opt) opt.hidden = !a.replay_available;
    const kv = (k, v) => [h("span", { text: k }), h("b", { text: v })];
    $("app-info").replaceChildren(...kv("Sürüm", a.version), ...kv("Veri klasörü", a.data_dir), ...kv("Sistem", a.platform));
  } catch (_) { /* not critical */ }
}
for (const [id, key] of [["p-autostart", "autostart"], ["p-resume", "resume_bot"], ["p-notify", "notify_desktop"], ["p-awake", "prevent_sleep"]])
  $(id).addEventListener("change", async () => { try { await api("PUT", "/api/app/prefs", { [key]: $(id).checked }); toast("Kaydedildi", "ok"); } catch (x) { toast(x.message, "err"); $(id).checked = !$(id).checked; } });
$("p-remember").addEventListener("change", async () => {
  try { const r = await api("POST", "/api/auth/remember", { enable: $("p-remember").checked }); $("p-remember").checked = r.remembered; toast(r.remembered ? "Bu bilgisayarda hatırlanacak" : "Hatırlama kapatıldı", "ok"); }
  catch (x) { toast(x.message, "err"); $("p-remember").checked = false; }
});

// ---------------------------------------------------------------- telegram
for (let i = 0; i < 24; i++) $("tg-hour").append(h("option", { value: String(i), text: String(i).padStart(2, "0") + ":00" }));
async function loadTelegram() {
  try { renderTelegram(await api("GET", "/api/telegram")); } catch (x) { toast(x.message, "err"); }
}
function renderTelegram(t) {
  S.tg = t; const b = $("tg-state");
  b.textContent = !t.configured ? "kapalı" : t.status === "ok" ? "bağlı · @" + t.bot_username : t.status === "error" ? "hata" : "bağlanıyor";
  b.className = "badge " + (t.status === "ok" ? "ok" : t.status === "error" ? "err" : "");
  $("tg-paired-text").textContent = t.paired ? "Eşleşti: " + t.chat_name : t.configured ? "Henüz eşleşmedi" : "Önce token'ı kaydet";
  if (t.error) $("tg-paired-text").textContent += " · " + t.error;
  $("btn-tg-pair").disabled = !t.configured; $("btn-tg-pair").textContent = t.paired ? "Yeniden eşleştir" : "Eşleştir";
  $("btn-tg-unpair").classList.toggle("hidden", !t.paired); $("btn-tg-test").disabled = !t.paired; $("btn-tg-delete").disabled = !t.configured;
  const p = t.pairing;
  $("tg-code-box").classList.toggle("hidden", !p || t.paired);
  if (p && !t.paired) {
    $("tg-code").textContent = "/eslestir " + p.code;
    const u = t.bot_username; $("tg-open").classList.toggle("hidden", !u); if (u) $("tg-open").href = "https://t.me/" + encodeURIComponent(u);
    $("tg-countdown").textContent = "kalan süre " + Math.max(0, Math.round((p.expires - Date.now()) / 60000)) + " dk";
  }
  const pr = t.prefs; $("tg-n-trades").checked = pr.notify_trades; $("tg-n-errors").checked = pr.notify_errors;
  $("tg-daily").checked = pr.daily_summary; $("tg-hour").value = String(pr.daily_summary_hour); $("tg-control").checked = pr.allow_control;
}
$("tg-token-form").addEventListener("submit", async (e) => {
  e.preventDefault(); const tok = $("tg-token").value.trim(); if (!tok) return toast("Token'ı yapıştır", "err");
  $("btn-tg-save").disabled = true;
  try { renderTelegram(await api("PUT", "/api/telegram/token", { token: tok })); $("tg-token").value = ""; toast("Telegram botu bağlandı. Şimdi 'Eşleştir'e bas.", "ok"); }
  catch (x) { toast(x.message, "err"); } finally { $("btn-tg-save").disabled = false; }
});
$("btn-tg-delete").addEventListener("click", async () => {
  if (!await confirmBox("Telegram bağlantısı silinsin mi?", "Token ve eşleşme silinecek; telefondan komut ve bildirim gelmez.", "Sil")) return;
  try { renderTelegram(await api("DELETE", "/api/telegram")); toast("Silindi", "ok"); } catch (x) { toast(x.message, "err"); }
});
$("btn-tg-pair").addEventListener("click", async () => {
  try {
    await api("POST", "/api/telegram/pair"); await loadTelegram();
    clearInterval(S.tgPoll);
    S.tgPoll = setInterval(async () => {
      try { const t = await api("GET", "/api/telegram"); renderTelegram(t);
        if (t.paired) { clearInterval(S.tgPoll); toast("Telefon eşleşti: " + t.chat_name, "ok"); }
        if (!t.pairing && !t.paired) clearInterval(S.tgPoll); } catch (_) { clearInterval(S.tgPoll); }
    }, 2000);
  } catch (x) { toast(x.message, "err"); }
});
$("btn-tg-unpair").addEventListener("click", async () => { try { renderTelegram(await api("POST", "/api/telegram/unpair")); toast("Eşleşme kaldırıldı", "ok"); } catch (x) { toast(x.message, "err"); } });
$("btn-tg-test").addEventListener("click", async () => { try { await api("POST", "/api/telegram/test"); toast("Test mesajı gönderildi", "ok"); } catch (x) { toast(x.message, "err"); } });
for (const [id, key, conv] of [["tg-n-trades", "notify_trades"], ["tg-n-errors", "notify_errors"], ["tg-daily", "daily_summary"], ["tg-control", "allow_control"], ["tg-hour", "daily_summary_hour", Number]])
  $(id).addEventListener("change", async () => {
    try { renderTelegram(await api("PUT", "/api/telegram/prefs", { [key]: conv ? conv($(id).value) : $(id).checked })); toast("Kaydedildi", "ok"); } catch (x) { toast(x.message, "err"); }
  });

// ---------------------------------------------------------------- keys
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
$("btn-keys-del").addEventListener("click", async () => { if (!await confirmBox("API anahtarı silinsin mi?", "Kayıtlı Binance anahtarı bu bilgisayardan silinecek.", "Sil")) return; try { await api("DELETE", "/api/keys"); toast("Silindi", "ok"); loadKeys(); } catch (x) { toast(x.message, "err"); } });
for (const [id, venue] of [["btn-check-test", "testnet"], ["btn-check-live", "live"]])
  $(id).addEventListener("click", async () => { try { renderCheck(await api("POST", "/api/keys/check", { venue })); } catch (x) { toast(x.message, "err"); } });

// ---------------------------------------------------------------- model
async function loadModel() {
  try {
    const m = await api("GET", "/api/model");
    for (const [side, el] of [["long", $("model-long")], ["short", $("model-short")]]) {
      const d = m[side] || {}; el.replaceChildren();
      el.append(h("div", { class: "meta" }, h("span", { class: "badge", text: "süre " + d.H + " mum" }),
        h("span", { class: "badge", text: d.tp_atr ? "TP " + d.tp_atr + "×ATR" : "TP yok" }), h("span", { class: "badge", text: d.sl_atr ? "SL " + d.sl_atr + "×ATR" : "SL yok" }),
        h("span", { class: "badge ok", text: "acil stop %" + (m.emergency_stop_pct ?? "–") + " (her işlemde)" })));
      if (!d.rules || !d.rules.length) el.append(h("div", { class: "muted", text: "Bu yönde doğrulamadan geçen kural yok." }));
      (d.exit_rules || []).forEach((r) => el.append(h("div", { class: "rule" }, h("b", { text: "ÇIK (kâr al)  " }),
        ...r.flatMap((c, j) => [j ? h("span", { class: "and", text: "  VE  " }) : null, c[0] + " " + c[1] + " " + Number(c[2]).toFixed(4)]))));
      (d.rules || []).forEach((r, i) => el.append(h("div", { class: "rule" }, h("b", { text: "#" + (i + 1) + "  " }),
        ...r.flatMap((c, j) => [j ? h("span", { class: "and", text: "  VE  " }) : null, c[0] + " " + c[1] + " " + Number(c[2]).toFixed(4)]))));
    }
    const rows = (m.stats && m.stats.rows) || [];
    table($("model-stats"), [["Grup", (r) => r.group], ["Yön", (r) => r.side], ["İşlem", (r) => r.trades, null, "num"],
      ["Kazanma", (r) => "%" + fmt(100 * r.win_rate, 1), null, "num"], ["Net/işlem", (r) => pct(100 * r.avg_net, 2), (r) => cls(r.avg_net), "num"]], rows, "İstatistik yok");
  } catch (x) { toast(x.message, "err"); }
}

boot().catch((x) => toast(x.message, "err"));
