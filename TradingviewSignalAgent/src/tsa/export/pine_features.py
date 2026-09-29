"""Pine v6 twins of every feature in features/registry.py.

`BASE` is emitted inside the model function; `FEATURES[name]` is a Pine
expression (or a list of statements whose last line assigns `<var>`) using
the base variables. Semantics mirror the Python code exactly, including the
`_safe_div` convention (b > 0 ? a / b : 0) and NaN -> 0 (nz) at the end.
"""
from __future__ import annotations

HELPERS = r"""
// ---------- helpers (mirror src/tsa/features/registry.py) ----------
sdiv(float a, float b) => b > 0 ? a / b : 0.0
ftanh(float x) =>
    float e = math.exp(2.0 * math.max(-20.0, math.min(20.0, x)))
    (e - 1.0) / (e + 1.0)
zc(float x, float mu, float sd) => math.max(-5.0, math.min(5.0, (nz(x) - mu) / sd))
decay(bool cond) =>
    int bs = ta.barssince(cond)
    na(bs) ? 0.0 : math.max(0.0, 1.0 - bs / 10.0)
// TradingView "RSI Divergence Indicator" logic (pivot left/right, range 5-60)
divs(float osc, simple int lbL, simple int lbR) =>
    bool plF = not na(ta.pivotlow(osc, lbL, lbR))
    bool phF = not na(ta.pivothigh(osc, lbL, lbR))
    int bsL = ta.barssince(plF[1])
    int bsH = ta.barssince(phF[1])
    bool inL = not na(bsL) and bsL >= 5 and bsL <= 60
    bool inH = not na(bsH) and bsH >= 5 and bsH <= 60
    float oR = osc[lbR]
    float lR = low[lbR]
    float hR = high[lbR]
    float plO = ta.valuewhen(plF, oR, 1)
    float plP = ta.valuewhen(plF, lR, 1)
    float phO = ta.valuewhen(phF, oR, 1)
    float phP = ta.valuewhen(phF, hR, 1)
    bool bull  = plF and lR < plP and oR > plO and inL
    bool hbull = plF and lR > plP and oR < plO and inL
    bool bear  = phF and hR > phP and oR < phO and inH
    bool hbear = phF and hR < phP and oR > phO and inH
    [bull, hbull, bear, hbear]
mfi14() =>
    float ch = ta.change(hlc3)
    float up = math.sum(volume * (ch <= 0 ? 0.0 : hlc3), 14)
    float dn = math.sum(volume * (ch >= 0 ? 0.0 : hlc3), 14)
    dn == 0 ? 100.0 : 100.0 - 100.0 / (1.0 + up / dn)
lrslope(float src, simple int n) => ta.linreg(src, n, 0) - ta.linreg(src, n, 1)
"""

# statements always present inside the model function
BASE = r"""
    float atr14 = ta.atr(14)
    float rsi = ta.rsi(close, 14)
    [macdL, sigL, hist] = ta.macd(close, 12, 26, 9)
    float rng = high - low
    float body = rng > 0 ? (close - open) / rng : 0.0
    float upw = rng > 0 ? (high - math.max(open, close)) / rng : 0.0
    float loww = rng > 0 ? (math.min(open, close) - low) / rng : 0.0
    float ret = close / close[1] - 1.0
    float vsma20 = ta.sma(volume, 20)
    float mfiV = mfi14()
    float ema50 = ta.ema(close, 50)
    float sv = nz(math.sign(ta.change(close))) * volume
    float obv = ta.cum(sv)
"""

# name -> (statements list, expression). Statements may define helper vars.
F: dict[str, tuple[list[str], str]] = {
    # candle
    "body": ([], "body"),
    "upwick": ([], "upw"),
    "lowwick": ([], "loww"),
    "clv": ([], "rng > 0 ? 2.0 * sdiv(close - low, rng) - 1.0 : 0.0"),
    "range_atr": ([], "sdiv(rng, atr14)"),
    "ret1_atr": ([], "sdiv(close - close[1], atr14)"),
    "ret3_atr": ([], "sdiv(close - close[3], atr14)"),
    "ret5_atr": ([], "sdiv(close - close[5], atr14)"),
    "ret10_atr": ([], "sdiv(close - close[10], atr14)"),
    "streak": (["var float stk = 0.0",
                "stk := close > close[1] ? (stk > 0 ? stk + 1 : 1.0) : close < close[1] ? (stk < 0 ? stk - 1 : -1.0) : 0.0"],
               "math.max(-10.0, math.min(10.0, stk)) / 5.0"),
    "engulf": ([], "(close > open and close[1] < open[1] and close >= open[1] and open <= close[1]) ? 1.0 : "
                   "(close < open and close[1] > open[1] and close <= open[1] and open >= close[1]) ? -1.0 : 0.0"),
    "pin": ([], "(loww > 0.6 and upw < 0.15) ? 1.0 : (upw > 0.6 and loww < 0.15) ? -1.0 : 0.0"),
    "chan20": (["float hh20 = ta.highest(high, 20)", "float ll20 = ta.lowest(low, 20)"],
               "hh20 > ll20 ? 2.0 * sdiv(close - ll20, hh20 - ll20) - 1.0 : 0.0"),
    "chan50": (["float hh50 = ta.highest(high, 50)", "float ll50 = ta.lowest(low, 50)"],
               "hh50 > ll50 ? 2.0 * sdiv(close - ll50, hh50 - ll50) - 1.0 : 0.0"),
    "chan100": (["float hh100 = ta.highest(high, 100)", "float ll100 = ta.lowest(low, 100)"],
                "hh100 > ll100 ? 2.0 * sdiv(close - ll100, hh100 - ll100) - 1.0 : 0.0"),
    # momentum
    "rsi": ([], "(rsi - 50.0) / 50.0"),
    "rsi7": ([], "(ta.rsi(close, 7) - 50.0) / 50.0"),
    "rsi21": ([], "(ta.rsi(close, 21) - 50.0) / 50.0"),
    "rsi_slope3": ([], "(rsi - rsi[3]) / 50.0"),
    "rsi_z": ([], "sdiv(rsi - ta.sma(rsi, 50), ta.stdev(rsi, 50))"),
    "macd_atr": ([], "sdiv(macdL, atr14)"),
    "sig_atr": ([], "sdiv(sigL, atr14)"),
    "hist_atr": ([], "sdiv(hist, atr14)"),
    "hist_slope": ([], "sdiv(hist - hist[1], atr14)"),
    "hist_slope3": ([], "sdiv(hist - hist[3], atr14)"),
    "macd_cross": ([], "(macdL > sigL and macdL[1] <= sigL[1]) ? 1.0 : (macdL < sigL and macdL[1] >= sigL[1]) ? -1.0 : 0.0"),
    "mfi": ([], "(mfiV - 50.0) / 50.0"),
    "adx": (["[dip, dim, adxV] = ta.dmi(14, 14)"], "adxV / 50.0"),
    "di": (["[dip, dim, adxV] = ta.dmi(14, 14)"], "(dip - dim) / 50.0"),
    "ema50_dist": ([], "sdiv(close - ema50, atr14)"),
    "ema200_dist": ([], "sdiv(close - ta.ema(close, 200), atr14)"),
    "ema50_slope": ([], "sdiv(ema50 - ema50[5], atr14)"),
    "atr_pct": ([], "sdiv(atr14, close) * 100.0"),
    "atr_ratio": ([], "sdiv(ta.atr(5), ta.atr(50))"),
    "cci": ([], "sdiv(hlc3 - ta.sma(hlc3, 20), 0.015 * ta.dev(hlc3, 20)) / 100.0"),
    "wt": (["float wEsa = ta.ema(hlc3, 10)", "float wD = ta.ema(math.abs(hlc3 - wEsa), 10)",
            "float wt1 = ta.ema(sdiv(hlc3 - wEsa, 0.015 * wD), 11)"], "wt1 / 50.0"),
    "wt_diff": (["float wEsa = ta.ema(hlc3, 10)", "float wD = ta.ema(math.abs(hlc3 - wEsa), 10)",
                 "float wt1 = ta.ema(sdiv(hlc3 - wEsa, 0.015 * wD), 11)"], "(wt1 - ta.sma(wt1, 4)) / 10.0"),
    "stoch": (["float hh14 = ta.highest(high, 14)", "float ll14 = ta.lowest(low, 14)"],
              "hh14 > ll14 ? 2.0 * sdiv(close - ll14, hh14 - ll14) - 1.0 : 0.0"),
    # volume
    "relvol": ([], "math.log(sdiv(volume + 1e-9, vsma20 + 1e-9) + 1e-9)"),
    "vol_z": ([], "sdiv(volume - ta.sma(volume, 50), ta.stdev(volume, 50))"),
    "svflow10": ([], "sdiv(math.sum(sv, 10), math.sum(volume, 10))"),
    "svflow30": ([], "sdiv(math.sum(sv, 30), math.sum(volume, 30))"),
    "cmf20": ([], "sdiv(math.sum(sdiv((close - low) - (high - close), rng) * volume, 20), math.sum(volume, 20))"),
    "vol_trend": ([], "math.log(sdiv(ta.sma(volume, 5) + 1e-9, vsma20 + 1e-9) + 1e-9)"),
    # divergences
    "divc14": ([], "ftanh(sdiv(lrslope(close, 14) * 14.0, 2.0 * atr14)) - ftanh(lrslope(rsi, 14) * 14.0 / 20.0)"),
    # time (UTC, bar open time); Pine dayofweek: 1=Sun..7=Sat -> Python Mon=0..Sun=6
    "hour_sin": (["float hrU = hour(time, 'UTC') + minute(time, 'UTC') / 60.0"], "math.sin(2.0 * math.pi * hrU / 24.0)"),
    "hour_cos": (["float hrU = hour(time, 'UTC') + minute(time, 'UTC') / 60.0"], "math.cos(2.0 * math.pi * hrU / 24.0)"),
    "dow_sin": (["float dowU = (dayofweek(time, 'UTC') + 5) % 7"], "math.sin(2.0 * math.pi * dowU / 7.0)"),
    "dow_cos": (["float dowU = (dayofweek(time, 'UTC') + 5) % 7"], "math.cos(2.0 * math.pi * dowU / 7.0)"),
}

for osc, var in (("rsi", "rsi"), ("hist", "hist")):
    st = f"[{osc}Bu, {osc}HBu, {osc}Be, {osc}HBe] = divs({var}, 5, 3)"
    F[f"div_{osc}_bull"] = ([st], f"decay({osc}Bu)")
    F[f"div_{osc}_hbull"] = ([st], f"decay({osc}HBu)")
    F[f"div_{osc}_bear"] = ([st], f"decay({osc}Be)")
    F[f"div_{osc}_hbear"] = ([st], f"decay({osc}HBe)")
_st5 = "[rsi5Bu, rsi5HBu, rsi5Be, rsi5HBe] = divs(rsi, 5, 5)"
F["div_rsi_bull5"] = ([_st5], "decay(rsi5Bu)")
F["div_rsi_bear5"] = ([_st5], "decay(rsi5Be)")

# correlations
_SERIES = {"ret": "ret", "aret": "math.abs(ret)", "body": "body", "close": "close", "rsi": "rsi", "obv": "obv",
           "vol": "volume", "rng": "rng", "hist": "hist", "mfi": "mfiV"}
_PAIRS = {"c_ret_vol": ("ret", "vol"), "c_aret_vol": ("aret", "vol"), "c_body_vol": ("body", "vol"),
          "c_close_rsi": ("close", "rsi"), "c_close_obv": ("close", "obv"), "c_close_vol": ("close", "vol"),
          "c_rng_vol": ("rng", "vol"), "c_close_hist": ("close", "hist"),
          "m_rsi_hist": ("rsi", "hist"), "m_rsi_mfi": ("rsi", "mfi"), "m_rsi_vol": ("rsi", "vol")}
for name, (a, b) in _PAIRS.items():
    for n in (10, 20, 50):
        F[f"{name}{n}"] = ([], f"ta.correlation({_SERIES[a]}, {_SERIES[b]}, {n})")


def _corr20_stmt(name):
    a, b = _PAIRS[name]
    return f"float {name}20v = ta.correlation({_SERIES[a]}, {_SERIES[b]}, 20)"


_P2 = {"cc_retvol_closersi": ("c_ret_vol", "c_close_rsi"), "cc_closeobv_closersi": ("c_close_obv", "c_close_rsi"),
       "cc_bodyvol_rsimfi": ("c_body_vol", "m_rsi_mfi"), "cc_closeobv_close": ("c_close_obv", None),
       "cc_bodyvol_close": ("c_body_vol", None)}
for name, (a, b) in _P2.items():
    for m in (20, 50):
        st = [_corr20_stmt(a)] + ([_corr20_stmt(b)] if b else [])
        F[f"{name}{m}"] = (st, f"ta.correlation({a}20v, {b + '20v' if b else 'close'}, {m})")
for name in ("c_close_obv", "c_close_rsi", "c_body_vol", "c_ret_vol"):
    F[f"d5_{name}20"] = ([_corr20_stmt(name)], f"{name}20v - {name}20v[5]")
for name in ("c_body_vol", "c_close_obv", "c_ret_vol"):
    F[f"z_{name}20"] = ([_corr20_stmt(name)],
                        f"sdiv({name}20v - ta.sma({name}20v, 100), ta.stdev({name}20v, 100))")

HTF_FEATURES = ["htf_rsi", "htf_hist_atr", "htf_ema50_dist", "htf_chan20"]
HTF_EXPR = ("[(ta.rsi(close, 14) - 50.0) / 50.0, sdiv(hist_, ta.atr(14)), sdiv(close - ta.ema(close, 50), ta.atr(14)), "
            "chan20_]")


def feature_block(names: list[str], indent: str = "    ") -> tuple[str, dict[str, str]]:
    """Pine statements computing `names`; returns (code, name -> pine variable)."""
    lines, seen, var_of = [], set(), {}
    for nm in names:
        if nm in HTF_FEATURES:
            continue
        stmts, expr = F[nm]
        for s in stmts:
            if s not in seen:
                seen.add(s)
                lines.append(s)
        v = "x_" + nm
        lines.append(f"float {v} = nz({expr})")
        var_of[nm] = v
    if any(n in HTF_FEATURES for n in names):
        lines.append("[htfA, htfB, htfC, htfD] = request.security(syminfo.tickerid, htfOf(tfi), htfTuple(), "
                     "lookahead = barmerge.lookahead_on)")
        for nm, v in zip(HTF_FEATURES, ("htfA", "htfB", "htfC", "htfD")):
            lines.append(f"float x_{nm} = nz({v})")
            var_of[nm] = f"x_{nm}"
    return "\n".join(indent + ln for ln in lines), var_of


HTF_FUNC = r"""
htfTuple() =>
    [m_, s_, hist_] = ta.macd(close, 12, 26, 9)
    float hh_ = ta.highest(high, 20)
    float ll_ = ta.lowest(low, 20)
    float chan20_ = hh_ > ll_ ? 2.0 * sdiv(close - ll_, hh_ - ll_) - 1.0 : 0.0
    float a_ = (ta.rsi(close, 14) - 50.0) / 50.0
    float b_ = sdiv(hist_, ta.atr(14))
    float c_ = sdiv(close - ta.ema(close, 50), ta.atr(14))
    // [1] + lookahead_on in the caller = value of the last CLOSED higher-TF bar (no repaint)
    [a_[1], b_[1], c_[1], chan20_[1]]
"""
