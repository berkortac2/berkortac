"""Feature registry: every feature has a Python implementation and a Pine v6
expression that computes exactly the same number on a TradingView chart.

Groups
  candle   - candle anatomy / short returns / channel position
  momentum - RSI, MACD, MFI, ADX, CCI, WaveTrend, Stoch, EMA distance, volatility
  volume   - relative volume, signed volume flow, CMF
  diverg   - pivot-based RSI / MACD-hist divergences (TradingView logic) + continuous
  corr_cm  - candle-metric rolling correlations
  corr_mm  - metric-metric rolling correlations
  corr2    - correlation-of-correlations, correlation change, correlation z-score
  time     - hour / weekday (intraday TFs only)
  htf      - higher-timeframe context from the last *closed* HTF bar

All features are causal (tests/test_causality.py) and scale free, so one
pooled model works on every coin.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from ..indicators import pine_ta as ta

WARMUP = 300


def _safe_div(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b > 0, a / np.where(b > 0, b, 1.0), 0.0)


@njit(cache=True)
def _streak(close):
    out = np.zeros(close.shape[0])
    for i in range(1, close.shape[0]):
        p = out[i - 1]
        if close[i] > close[i - 1]:
            out[i] = p + 1 if p > 0 else 1
        elif close[i] < close[i - 1]:
            out[i] = p - 1 if p < 0 else -1
    return out


def _tanh(x):
    return np.tanh(x)


def _decay(cond: np.ndarray, span: float = 10.0) -> np.ndarray:
    bs = ta.barssince(cond)
    return np.where(np.isnan(bs), 0.0, np.maximum(0.0, 1.0 - bs / span))


def divergences(osc, high, low, lbl=5, lbr=3, rmin=5, rmax=60):
    """TradingView 'RSI Divergence Indicator' logic, bar for bar."""
    pl = ~np.isnan(ta.pivotlow(osc, lbl, lbr))
    ph = ~np.isnan(ta.pivothigh(osc, lbl, lbr))
    osc_r, low_r, high_r = ta.shift(osc, lbr), ta.shift(low, lbr), ta.shift(high, lbr)

    def in_range(found):
        bs = ta.barssince(np.concatenate([[False], found[:-1]]))
        return (~np.isnan(bs)) & (bs >= rmin) & (bs <= rmax)

    pl_osc_prev = ta.valuewhen_prev(pl, osc_r, 1)
    pl_low_prev = ta.valuewhen_prev(pl, low_r, 1)
    ph_osc_prev = ta.valuewhen_prev(ph, osc_r, 1)
    ph_high_prev = ta.valuewhen_prev(ph, high_r, 1)
    rpl, rph = in_range(pl), in_range(ph)
    with np.errstate(invalid="ignore"):
        bull = pl & (low_r < pl_low_prev) & (osc_r > pl_osc_prev) & rpl
        hbull = pl & (low_r > pl_low_prev) & (osc_r < pl_osc_prev) & rpl
        bear = ph & (high_r > ph_high_prev) & (osc_r < ph_osc_prev) & rph
        hbear = ph & (high_r < ph_high_prev) & (osc_r > ph_osc_prev) & rph
    return bull, hbull, bear, hbear


def base_series(df: pd.DataFrame) -> dict:
    o, h, l, c, v = (df[k].to_numpy(np.float64) for k in ("open", "high", "low", "close", "volume"))
    x = {"open": o, "high": h, "low": l, "close": c, "volume": v}
    x["atr"] = ta.atr(h, l, c, 14)
    x["rsi"] = ta.rsi(c, 14)
    x["rsi7"] = ta.rsi(c, 7)
    x["rsi21"] = ta.rsi(c, 21)
    x["macd"], x["sig"], x["hist"] = ta.macd(c, 12, 26, 9)
    rng = h - l
    x["rng"] = rng
    x["body"] = _safe_div(c - o, rng)
    x["upw"] = _safe_div(h - np.maximum(o, c), rng)
    x["loww"] = _safe_div(np.minimum(o, c) - l, rng)
    x["ret"] = c / ta.shift(c) - 1.0
    x["aret"] = np.abs(x["ret"])
    x["vsma20"] = ta.sma(v, 20)
    x["mfi"] = ta.mfi(h, l, c, v, 14)
    x["dip"], x["dim"], x["adx"] = ta.dmi(h, l, c, 14, 14)
    x["ema50"] = ta.ema(c, 50)
    x["ema200"] = ta.ema(c, 200)
    ch = ta.change(c)
    sv = np.where(np.isnan(ch), 0.0, np.sign(ch)) * v
    x["sv"] = sv
    x["obv"] = np.cumsum(sv)
    hlc3 = (h + l + c) / 3.0
    x["hlc3"] = hlc3
    return x


def compute_features(df: pd.DataFrame, tf: str, htf: pd.DataFrame | None = None) -> pd.DataFrame:
    x = base_series(df)
    o, h, l, c, v = x["open"], x["high"], x["low"], x["close"], x["volume"]
    atr, rsi, hist, macd, sig = x["atr"], x["rsi"], x["hist"], x["macd"], x["sig"]
    f: dict[str, np.ndarray] = {}

    # ---- candle
    f["body"] = x["body"]
    f["upwick"] = x["upw"]
    f["lowwick"] = x["loww"]
    f["clv"] = np.where(x["rng"] > 0, 2 * _safe_div(c - l, x["rng"]) - 1, 0.0)
    f["range_atr"] = _safe_div(x["rng"], atr)
    for k in (1, 3, 5, 10):
        f[f"ret{k}_atr"] = _safe_div(c - ta.shift(c, k), atr)
    f["streak"] = np.clip(_streak(c), -10, 10) / 5.0
    po, pc = ta.shift(o), ta.shift(c)
    bull_e = (c > o) & (pc < po) & (c >= po) & (o <= pc)
    bear_e = (c < o) & (pc > po) & (c <= po) & (o >= pc)
    f["engulf"] = np.where(bull_e, 1.0, np.where(bear_e, -1.0, 0.0))
    ham = (x["loww"] > 0.6) & (x["upw"] < 0.15)
    sho = (x["upw"] > 0.6) & (x["loww"] < 0.15)
    f["pin"] = np.where(ham, 1.0, np.where(sho, -1.0, 0.0))
    for n in (20, 50, 100):
        hh, ll = ta.highest(h, n), ta.lowest(l, n)
        f[f"chan{n}"] = np.where(hh > ll, 2 * _safe_div(c - ll, hh - ll) - 1, 0.0)

    # ---- momentum
    f["rsi"] = (rsi - 50) / 50
    f["rsi7"] = (x["rsi7"] - 50) / 50
    f["rsi21"] = (x["rsi21"] - 50) / 50
    f["rsi_slope3"] = (rsi - ta.shift(rsi, 3)) / 50
    rsd = ta.stdev(rsi, 50)
    f["rsi_z"] = _safe_div(rsi - ta.sma(rsi, 50), rsd)
    f["macd_atr"] = _safe_div(macd, atr)
    f["sig_atr"] = _safe_div(sig, atr)
    f["hist_atr"] = _safe_div(hist, atr)
    f["hist_slope"] = _safe_div(hist - ta.shift(hist), atr)
    f["hist_slope3"] = _safe_div(hist - ta.shift(hist, 3), atr)
    pm, ps = ta.shift(macd), ta.shift(sig)
    f["macd_cross"] = np.where((macd > sig) & (pm <= ps), 1.0, np.where((macd < sig) & (pm >= ps), -1.0, 0.0))
    f["mfi"] = (x["mfi"] - 50) / 50
    f["adx"] = x["adx"] / 50
    f["di"] = (x["dip"] - x["dim"]) / 50
    f["ema50_dist"] = _safe_div(c - x["ema50"], atr)
    f["ema200_dist"] = _safe_div(c - x["ema200"], atr)
    f["ema50_slope"] = _safe_div(x["ema50"] - ta.shift(x["ema50"], 5), atr)
    f["atr_pct"] = _safe_div(atr, c) * 100
    f["atr_ratio"] = _safe_div(ta.atr(h, l, c, 5), ta.atr(h, l, c, 50))
    hlc3 = x["hlc3"]
    sm = ta.sma(hlc3, 20)
    dev = _mean_dev(hlc3, 20)
    f["cci"] = _safe_div(hlc3 - sm, 0.015 * dev) / 100
    esa = ta.ema(hlc3, 10)
    d = ta.ema(np.abs(hlc3 - esa), 10)
    ci = _safe_div(hlc3 - esa, 0.015 * d)
    wt1 = ta.ema(ci, 11)
    wt2 = ta.sma(wt1, 4)
    f["wt"] = wt1 / 50
    f["wt_diff"] = (wt1 - wt2) / 10
    hh14, ll14 = ta.highest(h, 14), ta.lowest(l, 14)
    f["stoch"] = np.where(hh14 > ll14, 2 * _safe_div(c - ll14, hh14 - ll14) - 1, 0.0)

    # ---- volume
    vs = x["vsma20"]
    f["relvol"] = np.log(_safe_div(v + 1e-9, vs + 1e-9) + 1e-9)
    vsd = ta.stdev(v, 50)
    f["vol_z"] = _safe_div(v - ta.sma(v, 50), vsd)
    for n in (10, 30):
        f[f"svflow{n}"] = _safe_div(ta.rolling_sum(x["sv"], n), ta.rolling_sum(v, n))
    mfv = _safe_div((c - l) - (h - c), x["rng"]) * v
    f["cmf20"] = _safe_div(ta.rolling_sum(mfv, 20), ta.rolling_sum(v, 20))
    f["vol_trend"] = np.log(_safe_div(ta.sma(v, 5) + 1e-9, vs + 1e-9) + 1e-9)

    # ---- divergences (pivot based, confirmed lbr bars later)
    for osc_name, osc in (("rsi", rsi), ("hist", hist)):
        bull, hbull, bear, hbear = divergences(osc, h, l, 5, 3)
        f[f"div_{osc_name}_bull"] = _decay(bull)
        f[f"div_{osc_name}_hbull"] = _decay(hbull)
        f[f"div_{osc_name}_bear"] = _decay(bear)
        f[f"div_{osc_name}_hbear"] = _decay(hbear)
    bull5, _, bear5, _ = divergences(rsi, h, l, 5, 5)
    f["div_rsi_bull5"] = _decay(bull5)
    f["div_rsi_bear5"] = _decay(bear5)
    sc = ta.linreg_slope(c, 14)
    sr = ta.linreg_slope(rsi, 14)
    f["divc14"] = _tanh(_safe_div(sc * 14, 2 * atr)) - _tanh(sr * 14 / 20)

    # ---- correlations: candle-metric and metric-metric
    pairs = {
        "c_ret_vol": (x["ret"], v), "c_aret_vol": (x["aret"], v), "c_body_vol": (x["body"], v),
        "c_close_rsi": (c, rsi), "c_close_obv": (c, x["obv"]), "c_close_vol": (c, v),
        "c_rng_vol": (x["rng"], v), "c_close_hist": (c, hist),
        "m_rsi_hist": (rsi, hist), "m_rsi_mfi": (rsi, x["mfi"]), "m_rsi_vol": (rsi, v),
    }
    corr20 = {}
    for name, (a, b) in pairs.items():
        for n in (10, 20, 50):
            r = ta.correlation(a, b, n)
            f[f"{name}{n}"] = r
            if n == 20:
                corr20[name] = r
    # ---- correlation of correlations / change / regime
    pairs2 = {
        "cc_retvol_closersi": (corr20["c_ret_vol"], corr20["c_close_rsi"]),
        "cc_closeobv_closersi": (corr20["c_close_obv"], corr20["c_close_rsi"]),
        "cc_bodyvol_rsimfi": (corr20["c_body_vol"], corr20["m_rsi_mfi"]),
        "cc_closeobv_close": (corr20["c_close_obv"], c),
        "cc_bodyvol_close": (corr20["c_body_vol"], c),
    }
    for name, (a, b) in pairs2.items():
        for m in (20, 50):
            f[f"{name}{m}"] = ta.correlation(a, b, m)
    for name in ("c_close_obv", "c_close_rsi", "c_body_vol", "c_ret_vol"):
        r = corr20[name]
        f[f"d5_{name}20"] = r - ta.shift(r, 5)
    for name in ("c_body_vol", "c_close_obv", "c_ret_vol"):
        r = corr20[name]
        f[f"z_{name}20"] = _safe_div(r - ta.sma(r, 100), ta.stdev(r, 100))

    # ---- time (UTC) - intraday only
    if tf in ("1m", "5m", "15m", "30m", "1h"):
        t = pd.to_datetime(df["open_time"].to_numpy(), unit="ms", utc=True)
        hr = t.hour.to_numpy() + t.minute.to_numpy() / 60.0
        dow = t.dayofweek.to_numpy()
        f["hour_sin"] = np.sin(2 * np.pi * hr / 24)
        f["hour_cos"] = np.cos(2 * np.pi * hr / 24)
        f["dow_sin"] = np.sin(2 * np.pi * dow / 7)
        f["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    # ---- higher-timeframe context (last closed HTF bar, Pine [1]+lookahead_on)
    if htf is not None and len(htf) > 30:
        hx = base_series(htf)
        hf = {
            "htf_rsi": (hx["rsi"] - 50) / 50,
            "htf_hist_atr": _safe_div(hx["hist"], hx["atr"]),
            "htf_ema50_dist": _safe_div(hx["close"] - hx["ema50"], hx["atr"]),
            "htf_chan20": np.where(ta.highest(hx["high"], 20) > ta.lowest(hx["low"], 20),
                                   2 * _safe_div(hx["close"] - ta.lowest(hx["low"], 20),
                                                 ta.highest(hx["high"], 20) - ta.lowest(hx["low"], 20)) - 1, 0.0),
        }
        hot = htf["open_time"].to_numpy()
        # index of the HTF bar that contains each chart bar
        idx = np.searchsorted(hot, df["open_time"].to_numpy(), side="right") - 1
        prev = idx - 1  # previous (closed) HTF bar
        for k, arr in hf.items():
            val = np.where(prev >= 0, arr[np.clip(prev, 0, None)], np.nan)
            f[k] = val

    out = pd.DataFrame(f)
    out = out.replace([np.inf, -np.inf], np.nan)
    return out


@njit(cache=True)
def _mean_dev(x, n):
    """Pine ta.dev: mean absolute deviation around the SMA."""
    out = np.full(x.shape[0], np.nan)
    for i in range(n - 1, x.shape[0]):
        w = x[i - n + 1:i + 1]
        m = w.mean()
        out[i] = np.abs(w - m).mean()
    return out


HTF_OF = {"1m": "15m", "5m": "1h", "15m": "4h", "30m": "4h", "1h": "4h", "4h": "1d", "1d": "1w", "1w": None}
