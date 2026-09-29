"""Generate the Pine v6 indicator + strategy from the frozen final models.

The generated script:
  * computes exactly the features used by the model (Pine twins in pine_features.py),
  * picks the parameter set of the chart's timeframe (1m/5m/15m/30m/1h/4h/1D/1W),
  * signals only on confirmed bars (no repaint), one position at a time with the
    same ATR TP/SL/time exit that was used in the backtest,
  * shows a multi-timeframe table (direction + score + tested success %),
  * fires alerts (alertcondition + JSON alert()).
"""
from __future__ import annotations

import math

from .pine_features import BASE, F, HELPERS, HTF_FEATURES, HTF_FUNC, feature_block

TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
PINE_TF = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
TF_LABEL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1s", "4h": "4s", "1d": "1G", "1w": "1H"}
HTF_OF = {"1m": "15", "5m": "60", "15m": "240", "30m": "240", "1h": "240", "4h": "D", "1d": "W", "1w": "W"}


def _num(x: float) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "0.0"
    s = f"{float(x):.8g}"
    if "e" in s or "E" in s:
        s = f"{float(x):.12f}".rstrip("0")
    if "." not in s:
        s += ".0"
    return s


# ---------------------------------------------------------------- score builders
def _linear(m: dict, var_of: dict) -> str:
    terms = [_num(m["b"])]
    for f, w, mu, sd in zip(m["features"], m["w"], m["mu"], m["sd"]):
        terms.append(f"{_num(w)} * zc({var_of[f]}, {_num(mu)}, {_num(sd)})")
    return " + ".join(terms)


def _rules_expr(rules: list, var_of: dict) -> str:
    ors = []
    for rule in rules:
        conds = []
        for f, op, v in rule:
            conds.append(f"{var_of[f]} {op} {_num(v)}")
        ors.append("(" + " and ".join(conds) + ")")
    return " or ".join(ors) if ors else "false"


def _tree_expr(node: dict, fnames: list, var_of: dict) -> str:
    if "leaf_value" in node:
        return _num(node["leaf_value"])
    f = fnames[node["split_feature"]]
    thr = _num(node["threshold"])
    left = _tree_expr(node["left_child"], fnames, var_of)
    right = _tree_expr(node["right_child"], fnames, var_of)
    return f"({var_of[f]} <= {thr} ? {left} : {right})"


def model_features(m: dict) -> list[str]:
    fam = m["family"]
    if fam in ("logit", "online_logit", "gbm", "knn"):
        return list(m["features"])
    if fam in ("rules", "meta"):
        out = []
        for rr in m["rules"].values():
            for rule in rr:
                out += [f for f, _, _ in rule]
        if fam == "meta":
            out += m["meta_features"]
        return list(dict.fromkeys(out))
    return []


def score_block(tfi: int, m: dict, var_of: dict, ind: str) -> list[str]:
    """Pine statements that set `sc` (score in [-1, 1]) for timeframe index tfi."""
    fam = m["family"]
    L = []
    if fam == "logit":
        L.append(f"{ind}sc := 2.0 / (1.0 + math.exp(-({_linear(m, var_of)}))) - 1.0")
    elif fam == "gbm":
        fn = m["features"]
        trees = [_tree_expr(t["tree_structure"], fn, var_of) for t in m["trees"]]
        L.append(f"{ind}float raw{tfi} = 0.0")
        for t in trees:
            L.append(f"{ind}raw{tfi} += {t}")
        L.append(f"{ind}sc := 2.0 / (1.0 + math.exp(-raw{tfi})) - 1.0")
    elif fam == "rules":
        L.append(f"{ind}bool rl{tfi} = {_rules_expr(m['rules']['1'], var_of)}")
        L.append(f"{ind}bool rs{tfi} = {_rules_expr(m['rules']['-1'], var_of)}")
        L.append(f"{ind}sc := rl{tfi} ? 1.0 : rs{tfi} ? -1.0 : 0.0")
    elif fam == "meta":
        mm = {"b": m["meta_b"], "features": m["meta_features"], "w": m["meta_w"], "mu": m["meta_mu"], "sd": m["meta_sd"]}
        L.append(f"{ind}float mz{tfi} = {_linear(mm, var_of)}")
        L.append(f"{ind}bool rl{tfi} = ({_rules_expr(m['rules']['1'], var_of)}) and mz{tfi} > 0")
        L.append(f"{ind}bool rs{tfi} = ({_rules_expr(m['rules']['-1'], var_of)}) and mz{tfi} < 0")
        L.append(f"{ind}sc := rl{tfi} ? 1.0 : rs{tfi} ? -1.0 : 0.0")
    elif fam == "online_logit":
        H = int(m["H"])
        n = len(m["features"])
        zs = ", ".join(f"zc({var_of[f]}, {_num(mu)}, {_num(sd)})" for f, mu, sd in zip(m["features"], m["mu"], m["sd"]))
        w0 = "0.0" if m.get("zero_prior") else ", ".join(_num(w) for w in m["w"])
        b0 = "0.0" if m.get("zero_prior") else _num(m["b"])
        L += [
            f"{ind}var array<float> w0_{tfi} = array.from({', '.join(['0.0'] * n) if m.get('zero_prior') else w0})",
            f"{ind}var array<float> w_{tfi} = array.copy(w0_{tfi})",
            f"{ind}var float b_{tfi} = {b0}",
            f"{ind}array<float> z_{tfi} = array.from({zs})",
            f"{ind}float zz{tfi} = b_{tfi}",
            f"{ind}for i = 0 to {n - 1}",
            f"{ind}    zz{tfi} += array.get(w_{tfi}, i) * array.get(z_{tfi}, i)",
            f"{ind}sc := 2.0 / (1.0 + math.exp(-zz{tfi})) - 1.0",
            f"{ind}// online update with the label of bar t-{H} (matured now)",
            f"{ind}var array<float> hist_{tfi} = array.new<float>()",
            f"{ind}for i = 0 to {n - 1}",
            f"{ind}    array.push(hist_{tfi}, array.get(z_{tfi}, i))",
            f"{ind}if array.size(hist_{tfi}) > {n} * ({H} + 1)",
            f"{ind}    for i = 0 to {n - 1}",
            f"{ind}        array.shift(hist_{tfi})",
            f"{ind}if array.size(hist_{tfi}) == {n} * ({H} + 1) and not na(atr14[{H}]) and atr14[{H}] > 0",
            f"{ind}    float mv = (close - open[{H - 1}]) / atr14[{H}]",
            f"{ind}    if math.abs(mv) > 0.1",
            f"{ind}        float yv = mv > 0 ? 1.0 : 0.0",
            f"{ind}        float zj = b_{tfi}",
            f"{ind}        for i = 0 to {n - 1}",
            f"{ind}            zj += array.get(w_{tfi}, i) * array.get(hist_{tfi}, i)",
            f"{ind}        float g = yv - 1.0 / (1.0 + math.exp(-zj))",
            f"{ind}        for i = 0 to {n - 1}",
            f"{ind}            float wi = array.get(w_{tfi}, i)",
            f"{ind}            array.set(w_{tfi}, i, wi + {_num(m['lr'])} * (g * array.get(hist_{tfi}, i) - {_num(m['l2'])} * (wi - array.get(w0_{tfi}, i))))",
            f"{ind}        b_{tfi} += {_num(m['lr'])} * g",
        ]
    elif fam == "knn":
        H, k, W, step = int(m["H"]), int(m["k"]), int(m["window"]), int(m["step"])
        fs = m["features"]
        n = len(fs)
        L += [
            f"{ind}// Lorentzian kNN, candidates sampled every {step} bars, labels matured after {H} bars",
            f"{ind}var array<float> kx_{tfi} = array.new<float>()",
            f"{ind}var array<float> ky_{tfi} = array.new<float>()",
            f"{ind}var array<int> kb_{tfi} = array.new<int>()",
            f"{ind}if bar_index >= {H} and (bar_index - {H}) % {step} == 0 and not na(atr14[{H}]) and atr14[{H}] > 0",
            f"{ind}    float mv = (close - open[{H - 1}]) / atr14[{H}]",
            f"{ind}    array.push(ky_{tfi}, mv > 0.1 ? 1.0 : mv < -0.1 ? -1.0 : 0.0)",
            f"{ind}    array.push(kb_{tfi}, bar_index - {H})",
        ] + [f"{ind}    array.push(kx_{tfi}, {var_of[f]}[{H}])" for f in fs] + [
            f"{ind}while array.size(kb_{tfi}) > 0 and bar_index - array.get(kb_{tfi}, 0) > {W}",
            f"{ind}    array.shift(kb_{tfi})",
            f"{ind}    array.shift(ky_{tfi})",
        ] + [f"{ind}    array.shift(kx_{tfi})" for _ in fs] + [
            f"{ind}array<float> bd = array.new<float>()",
            f"{ind}array<float> bl = array.new<float>()",
            f"{ind}int cnt = array.size(kb_{tfi})",
            f"{ind}if cnt >= {k}",
            f"{ind}    for j = 0 to cnt - 1",
            f"{ind}        float d = 0.0",
        ] + [f"{ind}        d += math.log(1.0 + math.abs({var_of[f]} - array.get(kx_{tfi}, j * {n} + {i})))"
             for i, f in enumerate(fs)] + [
            f"{ind}        if array.size(bd) < {k}",
            f"{ind}            array.push(bd, d)",
            f"{ind}            array.push(bl, array.get(ky_{tfi}, j))",
            f"{ind}        else",
            f"{ind}            int wI = array.indexof(bd, array.max(bd))",
            f"{ind}            if d < array.get(bd, wI)",
            f"{ind}                array.set(bd, wI, d)",
            f"{ind}                array.set(bl, wI, array.get(ky_{tfi}, j))",
            f"{ind}    sc := array.avg(bl)",
        ]
    else:
        raise ValueError(fam)
    return L


def generate(models: dict, stats: dict, family_label: str, strategy: bool = False) -> str:
    """models: tf -> export dict of the chosen family; stats: tf -> dict(dir_hit, win_rate, trades)."""
    tfs = [tf for tf in TFS if tf in models]
    feats = []
    for tf in tfs:
        feats += model_features(models[tf])
    feats = list(dict.fromkeys(feats))
    block, var_of = feature_block(feats)

    head = ("//@version=6\n"
            + (f'strategy("Tradingview Signal Agent [Strateji]", shorttitle = "TSA-S", overlay = true, '
               f'initial_capital = 10000, default_qty_type = strategy.percent_of_equity, default_qty_value = 10, '
               f'commission_type = strategy.commission.percent, commission_value = 0.05, slippage = 2, '
               f'pyramiding = 0, calc_on_every_tick = false, process_orders_on_close = false)\n'
               if strategy else
               'indicator("Tradingview Signal Agent", shorttitle = "TSA", overlay = true, '
               'max_labels_count = 500, max_lines_count = 500)\n'))
    doc = f"""// ============================================================================
// Tradingview Signal Agent  -  {family_label}
// Binance USDT-M perpetual (BINANCE:<COIN>USDT.P) grafikleri icin egitildi.
// Egitim: 20 coin, purged walk-forward + mum-mum replay; kilitli test donemi ve
// hic gorulmemis coinlerde dogrulandi. Sinyal sadece KAPANMIS mumda uretilir
// (repaint yok). Islem: sinyalden sonraki mumun acilisi, ATR tabanli TP/SL,
// H mum sonra zaman cikisi. Yatirim tavsiyesi degildir.
// Otomatik uretildi: src/tsa/export/pine_codegen.py
// ============================================================================
"""
    inputs = """
grpS = "Sinyal"
i_thrMult  = input.float(1.0, "Esik carpani (1.0 = test edilen ayar)", minval = 0.5, maxval = 3.0, step = 0.05, group = grpS, tooltip = "Buyuk deger = daha az ama daha secici sinyal")
i_showTPSL = input.bool(true, "TP / SL seviyelerini ciz", group = grpS)
i_showScore = input.bool(false, "Skoru alt panelde degil etikette goster", group = grpS)
grpT = "Coklu zaman dilimi tablosu"
i_table = input.bool(true, "Tabloyu goster", group = grpT)
i_tpos = input.string("Sag Ust", "Konum", options = ["Sag Ust", "Sag Alt", "Sol Ust", "Sol Alt"], group = grpT)
"""
    # per-TF constants
    def sw(fn, vals, typ):
        lines = [f"{fn}(simple int tfi) =>", "    switch tfi"]
        for i, tf in enumerate(TFS):
            v = vals.get(tf)
            if v is not None:
                lines.append(f"        {i} => {v}")
        lines.append(f"        => {vals.get('_default')}")
        return "\n".join(lines)

    H = {tf: str(int(models[tf]["H"])) for tf in tfs}
    H["_default"] = "10"
    TP = {tf: _num(models[tf]["tp_atr"] if models[tf]["tp_atr"] is not None else 1e6) for tf in tfs}
    TP["_default"] = "1.5"
    SL = {tf: _num(models[tf]["sl_atr"] if models[tf]["sl_atr"] is not None else 1e6) for tf in tfs}
    SL["_default"] = "1.5"
    TL = {tf: _num(models[tf].get("thr_long", 0.5)) for tf in tfs}
    TL["_default"] = "0.5"
    TS = {tf: _num(models[tf].get("thr_short", -0.5)) for tf in tfs}
    TS["_default"] = "-0.5"
    DH = {tf: _num(round(100 * stats.get(tf, {}).get("dir_hit", float("nan")), 1)) if tf in stats else "na" for tf in tfs}
    DH = {k: ("float(na)" if v == "na" else v) for k, v in DH.items()}
    DH["_default"] = "float(na)"
    WR = {tf: _num(round(100 * stats.get(tf, {}).get("win_rate", float("nan")), 1)) if tf in stats else "na" for tf in tfs}
    WR = {k: ("float(na)" if v == "na" else v) for k, v in WR.items()}
    WR["_default"] = "float(na)"
    htf = {tf: f'"{HTF_OF[tf]}"' for tf in TFS}
    htf["_default"] = '"D"'
    consts = "\n".join([
        sw("pH", H, "int"), sw("pTP", TP, "float"), sw("pSL", SL, "float"), sw("pThrL", TL, "float"),
        sw("pThrS", TS, "float"), sw("pDirHit", DH, "float"), sw("pWin", WR, "float"), sw("htfOf", htf, "string"),
    ])
    rule_family = models[tfs[0]]["family"] in ("rules", "meta")
    tf_index = """
tfIndexOf(simple int sec) =>
    sec <= 90 ? 0 : sec <= 450 ? 1 : sec <= 1350 ? 2 : sec <= 2700 ? 3 : sec <= 7200 ? 4 : sec <= 28800 ? 5 : sec <= 259200 ? 6 : 7
"""
    body = ["f_model(simple int tfi) =>", BASE.strip("\n"), block, "    float sc = 0.0", "    switch tfi"]
    for i, tf in enumerate(TFS):
        if tf not in models:
            continue
        body.append(f"        {i} =>")
        body += score_block(i, models[tf], var_of, "            ")
    body.append("    sc")
    model_fn = "\n".join(body)

    uses_htf = any(f in HTF_FEATURES for f in feats)
    tf_rows = []
    for i, tf in enumerate(TFS):
        if tf not in models:
            continue
        tf_rows.append(
            f'float sHi{i} = request.security(syminfo.tickerid, "{PINE_TF[tf]}", f_model({i})[1], lookahead = barmerge.lookahead_on)\n'
            f'float sLo{i} = request.security(syminfo.tickerid, "{PINE_TF[tf]}", f_model({i}))\n'
            f'float s{i} = timeframe.in_seconds("{PINE_TF[tf]}") > timeframe.in_seconds() ? sHi{i} : '
            f'timeframe.in_seconds("{PINE_TF[tf]}") == timeframe.in_seconds() ? sc0[1] : sLo{i}')
    tf_rows = "\n".join(tf_rows)
    long_cond = "sc0 > 0" if rule_family else "sc0 >= thrL and sc0 > 0"
    short_cond = "sc0 < 0" if rule_family else "sc0 <= thrS and sc0 < 0"
    main = f"""
// ---------------------------------------------------------------- chart timeframe model
int   CT   = tfIndexOf(timeframe.in_seconds())
float sc0  = f_model(CT)
float atrC = ta.atr(14)
float thrL = pThrL(CT) > 0 ? math.min(pThrL(CT) * i_thrMult, 0.999) : pThrL(CT)
float thrS = pThrS(CT) < 0 ? math.max(pThrS(CT) * i_thrMult, -0.999) : pThrS(CT)
bool rawLong  = {long_cond}
bool rawShort = {short_cond}

// ---------------------------------------------------------------- one position at a time (identical to the backtest)
var int   pos    = 0
var int   sigBar = na
var float atrSig = na
var float entry  = na
var float tpP    = na
var float slP    = na
var int   nTr  = 0
var int   nWin = 0
var int   nHit = 0
var int   nDir = 0
var array<int>   qBar = array.new<int>()
var array<float> qEnt = array.new<float>()
var array<int>   qDir = array.new<int>()
float costRT = 2 * (0.0005 + 0.0002)
bool  exitNow = false
float exitPx  = na
if barstate.isconfirmed
    // direction hit is judged H bars after the signal (close[t+H] vs entry), like the backtest
    while array.size(qBar) > 0 and bar_index >= array.get(qBar, 0)
        nDir += 1
        nHit += array.get(qDir, 0) * (close - array.get(qEnt, 0)) > 0 ? 1 : 0
        array.shift(qBar)
        array.shift(qEnt)
        array.shift(qDir)
    if pos != 0 and bar_index > sigBar
        if bar_index == sigBar + 1
            entry := open
            tpP := entry + pos * pTP(CT) * atrSig
            slP := entry - pos * pSL(CT) * atrSig
            array.push(qBar, sigBar + pH(CT))
            array.push(qEnt, entry)
            array.push(qDir, pos)
            if i_showTPSL and pTP(CT) < 100
                line.new(bar_index, tpP, bar_index + pH(CT), tpP, color = color.new(color.green, 30), style = line.style_dashed)
                line.new(bar_index, slP, bar_index + pH(CT), slP, color = color.new(color.red, 30), style = line.style_dashed)
        if pos == 1
            if low <= slP
                exitNow := true
                exitPx := bar_index > sigBar + 1 ? math.min(slP, open) : slP
            else if high >= tpP
                exitNow := true
                exitPx := bar_index > sigBar + 1 ? math.max(tpP, open) : tpP
        else
            if high >= slP
                exitNow := true
                exitPx := bar_index > sigBar + 1 ? math.max(slP, open) : slP
            else if low <= tpP
                exitNow := true
                exitPx := bar_index > sigBar + 1 ? math.min(tpP, open) : tpP
        if not exitNow and bar_index >= sigBar + pH(CT)
            exitNow := true
            exitPx := close
        if exitNow
            float net = pos * (exitPx / entry - 1) - costRT
            nTr += 1
            nWin += net > 0 ? 1 : 0
            pos := 0

bool longSig  = barstate.isconfirmed and pos == 0 and rawLong
bool shortSig = barstate.isconfirmed and pos == 0 and rawShort and not longSig
if longSig or shortSig
    pos := longSig ? 1 : -1
    sigBar := bar_index
    atrSig := atrC
    string txt = (longSig ? "AL" : "SAT") + (i_showScore ? "\\n" + str.tostring(sc0, "#.##") : "")
    label.new(bar_index, longSig ? low : high, txt, style = longSig ? label.style_label_up : label.style_label_down,
         color = longSig ? color.new(color.teal, 0) : color.new(color.maroon, 0), textcolor = color.white, size = size.small)
    alert('{{"signal":"' + (longSig ? "BUY" : "SELL") + '","symbol":"' + syminfo.tickerid + '","tf":"' + timeframe.period
         + '","price":' + str.tostring(close) + ',"atr":' + str.tostring(atrC) + ',"tp_atr":' + str.tostring(pTP(CT))
         + ',"sl_atr":' + str.tostring(pSL(CT)) + ',"hold_bars":' + str.tostring(pH(CT)) + '}}', alert.freq_once_per_bar_close)

alertcondition(longSig, "TSA AL", "TSA AL sinyali: {{{{ticker}}}} {{{{interval}}}} fiyat {{{{close}}}}")
alertcondition(shortSig, "TSA SAT", "TSA SAT sinyali: {{{{ticker}}}} {{{{interval}}}} fiyat {{{{close}}}}")
plotshape(longSig, "AL", shape.triangleup, location.belowbar, color.teal, display = display.data_window)
plotshape(shortSig, "SAT", shape.triangledown, location.abovebar, color.maroon, display = display.data_window)
plot(sc0, "TSA skor", display = display.data_window)
"""
    strat = """
// ---------------------------------------------------------------- strategy orders (Strategy Tester)
// entry at the next bar open; TP/SL in ticks relative to the real fill (active from the entry bar);
// time exit at the close of bar t+H
float tpTicks = pTP(CT) * atrC / syminfo.mintick
float slTicks = pSL(CT) * atrC / syminfo.mintick
if longSig
    strategy.entry("L", strategy.long)
    if pTP(CT) < 100
        strategy.exit("Lx", "L", profit = tpTicks, loss = slTicks)
if shortSig
    strategy.entry("S", strategy.short)
    if pTP(CT) < 100
        strategy.exit("Sx", "S", profit = tpTicks, loss = slTicks)
if strategy.position_size != 0 and bar_index >= sigBar + pH(CT)
    strategy.close_all(comment = "zaman", immediately = true)
"""
    dir_expr = ("s > 0 ? 1 : s < 0 ? -1 : 0" if rule_family
                else "(s >= tl and s > 0) ? 1 : (s <= ts and s < 0) ? -1 : 0")
    table = f"""
// ---------------------------------------------------------------- multi timeframe table
// higher TFs: last CLOSED bar (request.security + [1] + lookahead_on = no repaint)
// lower TFs : latest intrabar value; same TF: last closed chart bar
{tf_rows}
dirOf(float s, simple int tfi) =>
    float tl = pThrL(tfi) > 0 ? math.min(pThrL(tfi) * i_thrMult, 0.999) : pThrL(tfi)
    float ts = pThrS(tfi) < 0 ? math.max(pThrS(tfi) * i_thrMult, -0.999) : pThrS(tfi)
    na(s) ? 0 : {dir_expr}
posOf(string p) =>
    p == "Sag Ust" ? position.top_right : p == "Sag Alt" ? position.bottom_right : p == "Sol Ust" ? position.top_left : position.bottom_left
var table T = table.new(posOf(i_tpos), 5, 11, border_width = 1)
if i_table and barstate.islast
    table.cell(T, 0, 0, "TF", bgcolor = color.gray, text_color = color.white)
    table.cell(T, 1, 0, "Yon", bgcolor = color.gray, text_color = color.white)
    table.cell(T, 2, 0, "Skor", bgcolor = color.gray, text_color = color.white)
    table.cell(T, 3, 0, "Test yon %", bgcolor = color.gray, text_color = color.white)
    table.cell(T, 4, 0, "Test kazanc %", bgcolor = color.gray, text_color = color.white)
"""
    r = 1
    for i, tf in enumerate(TFS):
        if tf not in models:
            continue
        table += f"""    int d{i} = dirOf(s{i}, {i})
    table.cell(T, 0, {r}, "{TF_LABEL[tf]}", text_color = color.white, bgcolor = color.new(color.black, 20))
    table.cell(T, 1, {r}, d{i} == 1 ? "AL" : d{i} == -1 ? "SAT" : "-", text_color = color.white, bgcolor = d{i} == 1 ? color.teal : d{i} == -1 ? color.maroon : color.new(color.gray, 40))
    table.cell(T, 2, {r}, na(s{i}) ? "-" : str.tostring(s{i}, "#.##"), text_color = color.white, bgcolor = color.new(color.black, 20))
    table.cell(T, 3, {r}, na(pDirHit({i})) ? "-" : str.tostring(pDirHit({i}), "#.#"), text_color = color.white, bgcolor = color.new(color.black, 20))
    table.cell(T, 4, {r}, na(pWin({i})) ? "-" : str.tostring(pWin({i}), "#.#"), text_color = color.white, bgcolor = color.new(color.black, 20))
"""
        r += 1
    table += f"""    table.cell(T, 0, {r}, "Bu grafik", text_color = color.white, bgcolor = color.new(color.navy, 0))
    table.cell(T, 1, {r}, str.tostring(nTr) + " islem", text_color = color.white, bgcolor = color.new(color.navy, 0))
    table.cell(T, 2, {r}, "", bgcolor = color.new(color.navy, 0))
    table.cell(T, 3, {r}, nDir > 0 ? str.tostring(100.0 * nHit / nDir, "#.#") : "-", text_color = color.white, bgcolor = color.new(color.navy, 0))
    table.cell(T, 4, {r}, nTr > 0 ? str.tostring(100.0 * nWin / nTr, "#.#") : "-", text_color = color.white, bgcolor = color.new(color.navy, 0))
"""
    parts = [head, doc, inputs, HELPERS, HTF_FUNC if uses_htf else "", consts, tf_index, model_fn, main]
    parts.append(strat if strategy else table)
    return "\n".join(parts)
