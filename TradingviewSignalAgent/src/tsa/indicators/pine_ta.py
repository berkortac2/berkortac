"""Pine Script ``ta.*`` functions reimplemented bar-for-bar in numpy/numba.

Every function is causal (value at bar t only uses bars <= t) and follows
Pine's seeding rules (ema/rma start from the SMA of the first full window), so
a strategy found in Python yields the same numbers on a TradingView chart once
the warm-up bars have passed.
"""
from __future__ import annotations

import numpy as np
from numba import njit

NA = np.nan


@njit(cache=True)
def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape[0], NA)
    s, cnt = 0.0, 0
    for i in range(x.shape[0]):
        v = x[i]
        if np.isnan(v):
            s, cnt = 0.0, 0  # Pine: any na inside the window -> na
            continue
        s += v
        cnt += 1
        if cnt > n:
            s -= x[i - n]
            cnt = n
        if cnt == n:
            out[i] = s / n
    return out


@njit(cache=True)
def _recursive_ma(x: np.ndarray, n: int, alpha: float) -> np.ndarray:
    out = np.full(x.shape[0], NA)
    seed = sma(x, n)
    prev = NA
    for i in range(x.shape[0]):
        if np.isnan(prev):
            prev = seed[i]
        else:
            v = x[i]
            prev = alpha * v + (1.0 - alpha) * prev if not np.isnan(v) else prev
        out[i] = prev
    return out


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return _recursive_ma(np.asarray(x, np.float64), n, 2.0 / (n + 1))


def rma(x: np.ndarray, n: int) -> np.ndarray:
    return _recursive_ma(np.asarray(x, np.float64), n, 1.0 / n)


def change(x: np.ndarray, k: int = 1) -> np.ndarray:
    out = np.full(x.shape[0], NA)
    out[k:] = x[k:] - x[:-k]
    return out


def shift(x: np.ndarray, k: int = 1) -> np.ndarray:
    """Pine ``x[k]`` (history reference)."""
    out = np.full(x.shape[0], NA)
    if k < x.shape[0]:
        out[k:] = x[:-k] if k > 0 else x
    return out


def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    ch = change(close)
    up = rma(np.where(np.isnan(ch), NA, np.maximum(ch, 0.0)), n)
    dn = rma(np.where(np.isnan(ch), NA, np.maximum(-ch, 0.0)), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100.0 - 100.0 / (1.0 + up / dn)))
    out[np.isnan(up) | np.isnan(dn)] = NA
    return out


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    return line, signal, line - signal


def true_range(high, low, close) -> np.ndarray:
    pc = shift(close)
    tr = np.maximum(high - low, np.maximum(np.abs(high - pc), np.abs(low - pc)))
    tr[0] = high[0] - low[0]
    return tr


def atr(high, low, close, n: int = 14) -> np.ndarray:
    return rma(true_range(high, low, close), n)


@njit(cache=True)
def rolling_sum(x: np.ndarray, n: int) -> np.ndarray:
    return sma(x, n) * n


@njit(cache=True)
def stdev(x: np.ndarray, n: int) -> np.ndarray:
    """Pine ta.stdev (population / biased)."""
    out = np.full(x.shape[0], NA)
    for i in range(n - 1, x.shape[0]):
        w = x[i - n + 1:i + 1]
        if np.isnan(w).any():
            continue
        m = w.mean()
        out[i] = np.sqrt(((w - m) ** 2).mean())
    return out


@njit(cache=True)
def correlation(x: np.ndarray, y: np.ndarray, n: int) -> np.ndarray:
    """Pine ta.correlation: Pearson correlation over the last n bars."""
    out = np.full(x.shape[0], NA)
    for i in range(n - 1, x.shape[0]):
        a = x[i - n + 1:i + 1]
        b = y[i - n + 1:i + 1]
        if np.isnan(a).any() or np.isnan(b).any():
            continue
        ma, mb = a.mean(), b.mean()
        da, db = a - ma, b - mb
        va, vb = (da * da).mean(), (db * db).mean()
        if va <= 1e-30 or vb <= 1e-30:
            continue
        out[i] = (da * db).mean() / np.sqrt(va * vb)
    return out


@njit(cache=True)
def linreg_slope(x: np.ndarray, n: int) -> np.ndarray:
    """Least-squares slope of x over the last n bars (per bar)."""
    out = np.full(x.shape[0], NA)
    t = np.arange(n) * 1.0
    tm = t.mean()
    tv = ((t - tm) ** 2).sum()
    for i in range(n - 1, x.shape[0]):
        w = x[i - n + 1:i + 1]
        if np.isnan(w).any():
            continue
        out[i] = ((t - tm) * (w - w.mean())).sum() / tv
    return out


@njit(cache=True)
def highest(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape[0], NA)
    for i in range(n - 1, x.shape[0]):
        out[i] = x[i - n + 1:i + 1].max()
    return out


@njit(cache=True)
def lowest(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape[0], NA)
    for i in range(n - 1, x.shape[0]):
        out[i] = x[i - n + 1:i + 1].min()
    return out


@njit(cache=True)
def pivothigh(x: np.ndarray, left: int, right: int) -> np.ndarray:
    """Pine ta.pivothigh: at bar t returns x[t-right] if it is a pivot, else na.

    The pivot is only known ``right`` bars after it happened, exactly as on
    TradingView.
    """
    out = np.full(x.shape[0], NA)
    for i in range(left + right, x.shape[0]):
        c = i - right
        v = x[c]
        ok = True
        for j in range(c - left, c):
            if x[j] >= v:
                ok = False
                break
        if ok:
            for j in range(c + 1, i + 1):
                if x[j] > v:
                    ok = False
                    break
        if ok:
            out[i] = v
    return out


@njit(cache=True)
def pivotlow(x: np.ndarray, left: int, right: int) -> np.ndarray:
    out = np.full(x.shape[0], NA)
    for i in range(left + right, x.shape[0]):
        c = i - right
        v = x[c]
        ok = True
        for j in range(c - left, c):
            if x[j] <= v:
                ok = False
                break
        if ok:
            for j in range(c + 1, i + 1):
                if x[j] < v:
                    ok = False
                    break
        if ok:
            out[i] = v
    return out


def mfi(high, low, close, volume, n: int = 14) -> np.ndarray:
    src = (high + low + close) / 3.0
    ch = change(src)
    up = rolling_sum(np.where(np.isnan(ch), NA, np.where(ch <= 0, 0.0, src) * volume), n)
    dn = rolling_sum(np.where(np.isnan(ch), NA, np.where(ch >= 0, 0.0, src) * volume), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + up / dn)
    out = np.where(dn == 0, 100.0, out)
    out[np.isnan(up) | np.isnan(dn)] = NA
    return out


def dmi(high, low, close, di_len: int = 14, adx_len: int = 14):
    up = change(high)
    down = -change(low)
    plus_dm = np.where(np.isnan(up), NA, np.where((up > down) & (up > 0), up, 0.0))
    minus_dm = np.where(np.isnan(down), NA, np.where((down > up) & (down > 0), down, 0.0))
    tr = rma(true_range(high, low, close), di_len)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = 100 * rma(plus_dm, di_len) / tr
        minus = 100 * rma(minus_dm, di_len) / tr
        s = plus + minus
        adx = 100 * rma(np.abs(plus - minus) / np.where(s == 0, 1, s), adx_len)
    return plus, minus, adx


@njit(cache=True)
def barssince(cond: np.ndarray) -> np.ndarray:
    out = np.full(cond.shape[0], NA)
    last = -1
    for i in range(cond.shape[0]):
        if cond[i]:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


@njit(cache=True)
def valuewhen_prev(cond: np.ndarray, src: np.ndarray, occurrence: int) -> np.ndarray:
    """Pine ta.valuewhen(cond, src, occurrence)."""
    out = np.full(cond.shape[0], NA)
    buf = np.full(occurrence + 1, NA)
    cnt = 0
    for i in range(cond.shape[0]):
        if cond[i]:
            for k in range(occurrence, 0, -1):
                buf[k] = buf[k - 1]
            buf[0] = src[i]
            cnt += 1
        if cnt > occurrence:
            out[i] = buf[occurrence]
    return out
