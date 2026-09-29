"""Labels and trade outcomes.

For a signal on the close of bar t the trade is entered at open[t+1]
(what a TradingView strategy with process_orders_on_close=false does).

Triple barrier: TP = a*ATR(t), SL = b*ATR(t), time barrier H bars. When TP and
SL fall inside the same bar we assume SL first (conservative). Net return
subtracts fee+slippage on both sides.
"""
from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def trade_outcomes(o, h, l, c, atr, H, a, b, cost_rt, direction):
    """Return (net_ret, exit_idx, dir_hit) arrays for a hypothetical entry at every bar.

    direction: +1 long, -1 short. dir_hit = close[t+H] moved in trade direction
    versus the entry price (gross, no barriers).
    """
    n = o.shape[0]
    net = np.full(n, np.nan)
    ex = np.full(n, -1, np.int64)
    hit = np.full(n, np.nan)
    for t in range(n - 1):
        e = o[t + 1]
        A = atr[t]
        if np.isnan(A) or A <= 0 or t + H >= n:
            continue
        if direction > 0:
            tp, sl = e + a * A, e - b * A
        else:
            tp, sl = e - a * A, e + b * A
        exit_px = np.nan
        exit_i = t + H
        for j in range(t + 1, t + H + 1):
            if direction > 0:
                if l[j] <= sl:
                    exit_px = min(sl, o[j]) if j > t + 1 else sl
                    exit_i = j
                    break
                if h[j] >= tp:
                    exit_px = max(tp, o[j]) if j > t + 1 else tp
                    exit_i = j
                    break
            else:
                if h[j] >= sl:
                    exit_px = max(sl, o[j]) if j > t + 1 else sl
                    exit_i = j
                    break
                if l[j] <= tp:
                    exit_px = min(tp, o[j]) if j > t + 1 else tp
                    exit_i = j
                    break
        if np.isnan(exit_px):
            exit_px = c[t + H]
        g = (exit_px / e - 1.0) * direction
        net[t] = g - cost_rt
        ex[t] = exit_i
        hit[t] = 1.0 if (c[t + H] - e) * direction > 0 else 0.0
    return net, ex, hit


@njit(cache=True)
def simulate(sig_long, sig_short, net_l, ex_l, hit_l, net_s, ex_s, hit_s, seg_start, seg_end):
    """Non-overlapping trade simulation (one position at a time per coin).

    seg_start/seg_end delimit each coin's rows inside the concatenated arrays.
    Returns arrays of (row, direction, net, hit) for every taken trade.
    """
    cap = 0
    for s in range(seg_start.shape[0]):
        cap += seg_end[s] - seg_start[s]
    rows = np.empty(cap, np.int64)
    dirs = np.empty(cap, np.int8)
    nets = np.empty(cap)
    hits = np.empty(cap)
    k = 0
    for s in range(seg_start.shape[0]):
        t = seg_start[s]
        end = seg_end[s]
        while t < end:
            if sig_long[t] and not np.isnan(net_l[t]) and ex_l[t] < end:
                rows[k] = t; dirs[k] = 1; nets[k] = net_l[t]; hits[k] = hit_l[t]; k += 1
                t = ex_l[t]
                continue
            if sig_short[t] and not np.isnan(net_s[t]) and ex_s[t] < end:
                rows[k] = t; dirs[k] = -1; nets[k] = net_s[t]; hits[k] = hit_s[t]; k += 1
                t = ex_s[t]
                continue
            t += 1
    return rows[:k], dirs[:k], nets[:k], hits[:k]
