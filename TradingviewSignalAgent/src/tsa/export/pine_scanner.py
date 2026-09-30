"""Pine v6 multi-coin 5m scanner.

One request.security() per coin (TradingView allows 40 unique requests; 64 on
Ultimate), so the 1h context features are rebuilt INSIDE the 5m context from
5m candles (hourly OHLC aggregation + incremental RSI/MACD/ATR/EMA/channel)
instead of a nested request. `hourly_mirror()` is the line-by-line Python twin
used by the tests to prove it equals the research features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .pine_features import BASE, HELPERS, HTF_FEATURES, feature_block

DEFAULT_COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "NEARUSDT", "UNIUSDT", "ADAUSDT",
    "LINKUSDT", "AVAXUSDT", "BCHUSDT", "AAVEUSDT", "FILUSDT", "LTCUSDT", "XLMUSDT", "DOTUSDT",
    "1000SHIBUSDT", "TRXUSDT", "HBARUSDT", "1000PEPEUSDT", "SUIUSDT", "ENAUSDT", "WLDUSDT", "TAOUSDT",
    "ARBUSDT", "ONDOUSDT", "INJUSDT", "FETUSDT", "APTUSDT", "ETCUSDT", "OPUSDT", "CRVUSDT", "TIAUSDT",
    "ICPUSDT", "WIFUSDT"]

HOURLY_FUNC = r"""
// Seeded running averages, identical to ta.rma / ta.ema (SMA seed) but updated only
// when an hour closes. st = [value, count, sum]
rmaUpd(array<float> st, float x, int n, bool isEma) =>
    if not na(x)
        float cnt = array.get(st, 1) + 1
        array.set(st, 1, cnt)
        if cnt <= n
            array.set(st, 2, array.get(st, 2) + x)
            if cnt == n
                array.set(st, 0, array.get(st, 2) / n)
        else
            float a = isEma ? 2.0 / (n + 1) : 1.0 / n
            array.set(st, 0, a * x + (1 - a) * array.get(st, 0))
    array.get(st, 0)

// 1h context rebuilt from 5m candles: values of the last CLOSED hour, exactly like
// request.security(sym, "60", expr[1], lookahead_on) in the main indicator.
hourlyCtx() =>
    var float cO = na
    var float cH = na
    var float cL = na
    var float cC = na
    var float pC = na
    var array<float> sUp = array.from(float(na), 0.0, 0.0)
    var array<float> sDn = array.from(float(na), 0.0, 0.0)
    var array<float> sF = array.from(float(na), 0.0, 0.0)
    var array<float> sS = array.from(float(na), 0.0, 0.0)
    var array<float> sG = array.from(float(na), 0.0, 0.0)
    var array<float> sA = array.from(float(na), 0.0, 0.0)
    var array<float> sE = array.from(float(na), 0.0, 0.0)
    var array<float> hh = array.new<float>()
    var array<float> ll = array.new<float>()
    var float fR = na
    var float fB = na
    var float fC = na
    var float fD = na
    bool newHour = nz(ta.change(time("60")), 1) != 0
    if newHour and not na(cC)
        float ch = na(pC) ? float(na) : cC - pC
        float up = rmaUpd(sUp, na(ch) ? float(na) : math.max(ch, 0.0), 14, false)
        float dn = rmaUpd(sDn, na(ch) ? float(na) : -math.min(ch, 0.0), 14, false)
        float rsiH = na(up) or na(dn) ? float(na) : dn == 0 ? 100.0 : up == 0 ? 0.0 : 100.0 - 100.0 / (1.0 + up / dn)
        float ef = rmaUpd(sF, cC, 12, true)
        float es = rmaUpd(sS, cC, 26, true)
        float macdH = na(ef) or na(es) ? float(na) : ef - es
        float sigH = rmaUpd(sG, macdH, 9, true)
        float histH = na(macdH) or na(sigH) ? float(na) : macdH - sigH
        float tr = na(pC) ? cH - cL : math.max(cH - cL, math.abs(cH - pC), math.abs(cL - pC))
        float atrH = rmaUpd(sA, tr, 14, false)
        float e50 = rmaUpd(sE, cC, 50, true)
        array.push(hh, cH)
        array.push(ll, cL)
        if array.size(hh) > 20
            array.shift(hh)
            array.shift(ll)
        float hi20 = array.size(hh) == 20 ? array.max(hh) : na
        float lo20 = array.size(ll) == 20 ? array.min(ll) : na
        fR := na(rsiH) ? float(na) : (rsiH - 50.0) / 50.0
        fB := na(histH) or na(atrH) ? float(na) : sdiv(histH, atrH)
        fC := na(e50) or na(atrH) ? float(na) : sdiv(cC - e50, atrH)
        fD := na(hi20) ? float(na) : hi20 > lo20 ? 2.0 * sdiv(cC - lo20, hi20 - lo20) - 1.0 : 0.0
        pC := cC
    if newHour or na(cO)
        cO := open
        cH := high
        cL := low
        cC := close
    else
        cH := math.max(cH, high)
        cL := math.min(cL, low)
        cC := close
    [fR, fB, fC, fD]
"""


def hourly_mirror(df5: pd.DataFrame) -> pd.DataFrame:
    """Python twin of hourlyCtx() (same seeding, same update order)."""
    def upd(st, x, n, ema):
        if x is None or np.isnan(x):
            return st[0]
        st[1] += 1
        if st[1] <= n:
            st[2] += x
            if st[1] == n:
                st[0] = st[2] / n
        else:
            a = 2.0 / (n + 1) if ema else 1.0 / n
            st[0] = a * x + (1 - a) * st[0]
        return st[0]

    def sdiv(a, b):
        return a / b if b > 0 else 0.0
    nan = float("nan")
    st = {k: [nan, 0, 0.0] for k in ("up", "dn", "f", "s", "g", "a", "e")}
    cO = cH = cL = cC = pC = nan
    hh, ll = [], []
    fR = fB = fC = fD = nan
    out = np.full((len(df5), 4), np.nan)
    hour = (df5["open_time"].to_numpy() // 3_600_000)
    o, h, l, c = (df5[k].to_numpy() for k in ("open", "high", "low", "close"))
    for i in range(len(df5)):
        new_hour = i == 0 or hour[i] != hour[i - 1]
        if new_hour and not np.isnan(cC):
            ch = nan if np.isnan(pC) else cC - pC
            up = upd(st["up"], nan if np.isnan(ch) else max(ch, 0.0), 14, False)
            dn = upd(st["dn"], nan if np.isnan(ch) else -min(ch, 0.0), 14, False)
            rsi = nan if (np.isnan(up) or np.isnan(dn)) else 100.0 if dn == 0 else 0.0 if up == 0 else 100 - 100 / (1 + up / dn)
            ef, es = upd(st["f"], cC, 12, True), upd(st["s"], cC, 26, True)
            macd = nan if (np.isnan(ef) or np.isnan(es)) else ef - es
            sig = upd(st["g"], macd, 9, True)
            hist = nan if (np.isnan(macd) or np.isnan(sig)) else macd - sig
            tr = cH - cL if np.isnan(pC) else max(cH - cL, abs(cH - pC), abs(cL - pC))
            atr = upd(st["a"], tr, 14, False)
            e50 = upd(st["e"], cC, 50, True)
            hh.append(cH); ll.append(cL)
            if len(hh) > 20:
                hh.pop(0); ll.pop(0)
            hi, lo = (max(hh), min(ll)) if len(hh) == 20 else (nan, nan)
            fR = nan if np.isnan(rsi) else (rsi - 50) / 50
            fB = nan if (np.isnan(hist) or np.isnan(atr)) else sdiv(hist, atr)
            fC = nan if (np.isnan(e50) or np.isnan(atr)) else sdiv(cC - e50, atr)
            fD = nan if np.isnan(hi) else (2 * sdiv(cC - lo, hi - lo) - 1 if hi > lo else 0.0)
            pC = cC
        if new_hour or np.isnan(cO):
            cO, cH, cL, cC = o[i], h[i], l[i], c[i]
        else:
            cH, cL, cC = max(cH, h[i]), min(cL, l[i]), c[i]
        out[i] = (fR, fB, fC, fD)
    return pd.DataFrame(out, columns=HTF_FEATURES)


def _rules_expr(rules, var_of):
    if not rules:
        return "false"
    return " or ".join("(" + " and ".join(f"{var_of[f]} {op} {float(v)!r}" for f, op, v in r) + ")" for r in rules)


def generate_scanner(model: dict, coins: list[str] | None = None, title: str = "TSA 5m Coin Tarayici") -> str:
    coins = (coins or DEFAULT_COINS)[:39]
    n = len(coins)
    L_, S_ = model["long"], model.get("short", {"rules": []})
    lr, sr, xr = L_["rules"], S_.get("rules", []), L_.get("exit_rules", [])
    feats = sorted({c[0] for r in lr + sr + xr for c in r})
    block, var_of = feature_block([f for f in feats if f not in HTF_FEATURES])
    uses_htf = any(f in HTF_FEATURES for f in feats)
    htf_lines = ""
    if uses_htf:
        htf_lines = "    [hA, hB, hC, hD] = hourlyCtx()\n" + "".join(
            f"    float x_{nm} = nz({v})\n" for nm, v in zip(HTF_FEATURES, ("hA", "hB", "hC", "hD")))
        for nm in HTF_FEATURES:
            var_of[nm] = f"x_{nm}"
    H = int(L_["H"])
    sl = float(L_.get("sl_atr") or 0.0)
    em = float(model.get("emergency_stop", 0.08))
    stats = model.get("stats_line", "")
    out = [f"""//@version=6
// {title} - Tradingview Signal Agent. {n} Binance USDT-M perpetual coins on 5 minutes.
// AL = long entry on the CLOSE of a 5m candle (no repaint). The scanner then follows each AL position like the
// bot does and shows CIK (take profit / exit) when WaveTrend reaches overbought, the stop is hit
// ({sl:g} x ATR or {em * 100:g}%) or {H} candles have passed. One alert lists every coin that just gave AL or CIK.
// Rules/parameters: replayed walk-forward research (reports/SONUCLAR_5DK_BOT.md). {stats}
// Not investment advice.
indicator("{title}", shorttitle = "TSA Scan 5m", overlay = true, calc_bars_count = 6000)
""", HELPERS, HOURLY_FUNC if uses_htf else "", f"""
scan() =>
{BASE.strip(chr(10))}
{block}
{htf_lines}    bool L = {_rules_expr(lr, var_of)}
    bool S = {_rules_expr(sr, var_of)}
    bool X = {_rules_expr(xr, var_of)}
    float sig = L ? 1.0 : S ? -1.0 : 0.0
    float xf = X ? 1.0 : 0.0
    // previous (closed) candle + lookahead_on in the caller = no repaint
    [sig[1], float(time[1]), close[1], xf[1], low[1], high[1], atr14[1], open[1]]

grp = "Coinler (Binance USDT-M perpetual, en fazla 39)"
i_exitKeep = input.int(12, "CIK tabloda kac mum kalsin", minval = 1, group = "Tablo")
i_onlyActive = input.bool(false, "Tabloda sadece aktif AL / yeni CIK goster", group = "Tablo")
i_pos = input.string("Sag Ust", "Tablo konumu", options = ["Sag Ust", "Sag Alt", "Sol Ust", "Sol Alt"], group = "Tablo")
i_H = input.int({H}, "En uzun tutma (mum)", minval = 1, group = "Cikis")
i_sl = input.float({sl}, "Zarar kes (x ATR, 0 = yok)", minval = 0, step = 0.5, group = "Cikis")
i_em = input.float({em * 100:g}, "Acil stop (%)", minval = 0, step = 0.5, group = "Cikis")
i_useX = input.bool(true, "CIK sinyali (WaveTrend asiri alim) ile kar al", group = "Cikis")
"""]
    for i, c in enumerate(coins):
        out.append(f'sym{i:02d} = input.symbol("BINANCE:{c}.P", "{i + 1}", group = grp, inline = "c{i // 3}")')
    out.append("")
    for i in range(n):
        out.append(f"[g{i:02d}, t{i:02d}, p{i:02d}, x{i:02d}, lo{i:02d}, hi{i:02d}, a{i:02d}, o{i:02d}] = request.security("
                   f"sym{i:02d}, \"5\", scan(), lookahead = barmerge.lookahead_on)")
    out.append(f"""
var array<string> names = array.new<string>({n}, "")
var array<int> dirA = array.new<int>({n}, 0)          // open position: 1 AL, -1 SAT, 0 none
var array<float> entA = array.new<float>({n}, na)
var array<float> stopA = array.new<float>({n}, na)
var array<float> atrA = array.new<float>({n}, na)       // ATR of the signal candle (stop distance)
var array<int> barsA = array.new<int>({n}, 0)
var array<float> lastPx = array.new<float>({n}, na)
var array<float> lastT = array.new<float>({n}, na)
var array<int> exitBar = array.new<int>({n}, -100000)
var array<float> exitNet = array.new<float>({n}, na)
var array<int> entBar = array.new<int>({n}, -100000)
upd(int i, string sym, float g, float t, float p, float x, float lo, float hi, float a, float o) =>
    string nm = str.replace(str.replace(sym, "BINANCE:", ""), ".P", "")
    array.set(names, i, nm)
    string res = ""
    float prevT = array.get(lastT, i)
    // one decision per NEW closed 5m candle of that coin, same rules as the research / bot:
    // entry at the open of the candle after the signal, then stop (gaps fill at the open), CIK, time; then entry
    if not na(t) and (na(prevT) or t != prevT)
        array.set(lastT, i, t)
        array.set(lastPx, i, p)
        int d = array.get(dirA, i)
        if d != 0
            if na(array.get(entA, i))
                // first candle of the position: the entry is its open, the stop is set from there
                float e0 = o
                float sa = array.get(atrA, i)
                float stp = i_em > 0 ? e0 * (1 - d * i_em / 100) : float(na)
                if i_sl > 0 and not na(sa)
                    float s2 = e0 - d * i_sl * sa
                    stp := na(stp) ? s2 : d > 0 ? math.max(stp, s2) : math.min(stp, s2)
                array.set(entA, i, e0)
                array.set(stopA, i, stp)
            array.set(barsA, i, array.get(barsA, i) + 1)
            float en = array.get(entA, i)
            float st = array.get(stopA, i)
            bool first = array.get(barsA, i) == 1
            float px = na
            if d > 0 and not na(st) and lo <= st
                px := first ? st : math.min(st, o)
            else if d < 0 and not na(st) and hi >= st
                px := first ? st : math.max(st, o)
            else if d > 0 and i_useX and x > 0
                px := p
            else if array.get(barsA, i) >= i_H
                px := p
            if not na(px)
                float net = d * (px / en - 1.0) - 0.0014
                array.set(dirA, i, 0)
                array.set(exitBar, i, bar_index)
                array.set(exitNet, i, net)
                res += "X:" + nm + " (" + str.tostring(net * 100, "#.##") + "%),"
        if array.get(dirA, i) == 0 and g != 0
            int nd = g > 0 ? 1 : -1
            array.set(dirA, i, nd)
            array.set(entA, i, na)          // filled at the next candle's open
            array.set(stopA, i, na)
            array.set(atrA, i, a)
            array.set(barsA, i, 0)
            array.set(entBar, i, bar_index)
            res += (nd > 0 ? "L:" : "S:") + nm + ","
    res
""")
    for i in range(n):
        out.append(f"r{i:02d} = upd({i}, sym{i:02d}, g{i:02d}, t{i:02d}, p{i:02d}, x{i:02d}, lo{i:02d}, hi{i:02d}, "
                   f"a{i:02d}, o{i:02d})")
    out.append("string fired = " + " + ".join(f"r{i:02d}" for i in range(n)))
    out.append(f"""
// ---------------------------------------------------------------- alert + table
// coins' candles can arrive on different ticks of the chart bar; `var` state is rolled back on every
// realtime tick, so `fired` repeats earlier coins. varip remembers what was already sent in this bar.
varip string sentKeys = ""
varip int sentBarT = na
string alL = ""
string alS = ""
string alX = ""
string nwL = ""
string nwS = ""
string nwX = ""
if barstate.isrealtime and (na(sentBarT) or time != sentBarT)
    sentKeys := ""
    sentBarT := time
for part in str.split(fired, ",")
    if part != ""
        bool isNew = barstate.isrealtime and not str.contains(sentKeys, "|" + part + "|")
        if isNew
            sentKeys += "|" + part + "|"
        string nm = str.substring(part, 2)
        if str.startswith(part, "L:")
            alL += (alL == "" ? "" : ", ") + nm
            nwL += isNew ? (nwL == "" ? "" : ", ") + nm : ""
        else if str.startswith(part, "S:")
            alS += (alS == "" ? "" : ", ") + nm
            nwS += isNew ? (nwS == "" ? "" : ", ") + nm : ""
        else if str.startswith(part, "X:")
            alX += (alX == "" ? "" : ", ") + nm
            nwX += isNew ? (nwX == "" ? "" : ", ") + nm : ""
if nwL != "" or nwS != "" or nwX != ""
    alert("TSA 5m" + (nwL != "" ? " | AL: " + nwL : "") + (nwS != "" ? " | SAT: " + nwS : "") + (nwX != "" ? " | ÇIK: " + nwX : ""), alert.freq_all)
alertcondition(alL != "" or alS != "", "TSA tarayici: yeni AL/SAT", "TSA 5m tarayici yeni AL/SAT sinyali verdi")
alertcondition(alX != "", "TSA tarayici: CIK (kar al)", "TSA 5m tarayici: bir AL pozisyonu icin CIK zamani")
plotshape(alL != "", "Yeni AL", shape.triangleup, location.bottom, color.teal, display = display.data_window)
plotshape(alX != "", "Yeni CIK", shape.xcross, location.top, color.orange, display = display.data_window)

posOf(string p) =>
    p == "Sag Ust" ? position.top_right : p == "Sag Alt" ? position.bottom_right : p == "Sol Ust" ? position.top_left : position.bottom_left
var table T = table.new(posOf(i_pos), 5, {n + 2}, border_width = 1)
if barstate.islast
    table.clear(T, 0, 0, 4, {n + 1})
    table.cell(T, 0, 0, "Coin", bgcolor = color.gray, text_color = color.white, text_size = size.small)
    table.cell(T, 1, 0, "Durum", bgcolor = color.gray, text_color = color.white, text_size = size.small)
    table.cell(T, 2, 0, "Mum", bgcolor = color.gray, text_color = color.white, text_size = size.small)
    table.cell(T, 3, 0, "Giris", bgcolor = color.gray, text_color = color.white, text_size = size.small)
    table.cell(T, 4, 0, "K/Z %", bgcolor = color.gray, text_color = color.white, text_size = size.small)
    int row = 1
    int nAct = 0
    for i = 0 to {n - 1}
        int d = array.get(dirA, i)
        int sinceX = bar_index - array.get(exitBar, i)
        bool xNew = d == 0 and sinceX < i_exitKeep
        nAct += d != 0 ? 1 : 0
        if d != 0 or xNew or not i_onlyActive
            float en = array.get(entA, i)
            float kz = d != 0 and not na(en) ? d * (array.get(lastPx, i) / en - 1.0) * 100 : xNew ? array.get(exitNet, i) * 100 : float(na)
            string st = d > 0 ? "AL" : d < 0 ? "SAT" : xNew ? "ÇIK" : "-"
            color bg = d > 0 ? color.teal : d < 0 ? color.maroon : xNew ? color.orange : color.new(color.black, 20)
            table.cell(T, 0, row, array.get(names, i), text_color = color.white, bgcolor = color.new(color.black, 20), text_size = size.small)
            table.cell(T, 1, row, st, text_color = color.white, bgcolor = bg, text_size = size.small)
            table.cell(T, 2, row, d != 0 ? str.tostring(array.get(barsA, i)) : xNew ? str.tostring(sinceX) : "", text_color = color.white, bgcolor = color.new(color.black, 20), text_size = size.small)
            table.cell(T, 3, row, d != 0 and not na(en) ? str.tostring(en) : d != 0 ? "..." : "", text_color = color.white, bgcolor = color.new(color.black, 20), text_size = size.small)
            table.cell(T, 4, row, na(kz) ? "" : str.tostring(kz, "#.##"), text_color = color.white, bgcolor = na(kz) ? color.new(color.black, 20) : kz >= 0 ? color.new(color.green, 40) : color.new(color.red, 40), text_size = size.small)
            row += 1
    table.cell(T, 0, row, timeframe.period == "5" ? str.tostring(nAct) + " acik AL" : "5 dk grafik kullanin!", text_color = color.white, bgcolor = timeframe.period == "5" ? color.navy : color.orange, text_size = size.small)
""")
    return "\n".join(out)
