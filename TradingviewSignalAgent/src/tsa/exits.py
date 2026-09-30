"""Exit-policy simulator for AL positions (research twin of the bot's exit logic):
stop first inside a bar, then take profit, then the indicator exit at the close, then the time limit."""
from __future__ import annotations

import numpy as np
from numba import njit

EXIT_FEATS = ["rsi", "ema50_dist", "hist_atr", "stoch", "cci", "wt", "chan20", "di"]


@njit(cache=True)
def sim_trades(o, h, l, c, atr, sig, F, seg_s, seg_e, Hmax, tp, sl, emerg, ta, td, ind, thr, cost):
    """Like sim() but also returns the signal bar and the exit bar index of every trade."""
    n = o.shape[0]
    out_t = np.empty(n, np.int64)
    out_x = np.empty(n, np.int64)
    out_net = np.empty(n)
    out_bars = np.empty(n)
    out_why = np.empty(n, np.int8)
    k = 0
    for s in range(seg_s.shape[0]):
        t = seg_s[s]
        e = seg_e[s]
        while t < e - 1:
            if not sig[t] or np.isnan(atr[t]) or atr[t] <= 0:
                t += 1
                continue
            A = atr[t]
            en = o[t + 1]
            tpP = en + tp * A if tp > 0 else 1e18
            stop = en * (1 - emerg)
            if sl > 0:
                stop = max(stop, en - sl * A)
            hh = en
            px = np.nan
            why = 0
            last = min(t + Hmax, e - 1)
            xi = last
            for j in range(t + 1, last + 1):
                if ta > 0 and hh >= en + ta * A:
                    stop = max(stop, hh - td * A)
                if l[j] <= stop:
                    px = stop if j == t + 1 else min(stop, o[j])
                    xi = j
                    why = 1
                    break
                if h[j] >= tpP:
                    px = tpP if j == t + 1 else max(tpP, o[j])
                    xi = j
                    why = 2
                    break
                hh = max(hh, h[j])
                if ind >= 0 and F[j, ind] >= thr:
                    px = c[j]
                    xi = j
                    why = 3
                    break
            if np.isnan(px):
                px = c[xi]
            out_t[k] = t
            out_x[k] = xi
            out_net[k] = px / en - 1.0 - cost
            out_bars[k] = xi - t
            out_why[k] = why
            k += 1
            t = xi
    return out_t[:k], out_x[:k], out_net[:k], out_bars[:k], out_why[:k]


@njit(cache=True)
def sim(o, h, l, c, atr, sig, F, seg_s, seg_e, Hmax, tp, sl, emerg, ta, td, ind, thr, cost):
    _, _, net, bars, why = sim_trades(o, h, l, c, atr, sig, F, seg_s, seg_e, Hmax, tp, sl, emerg, ta, td, ind,
                                      thr, cost)
    return net, bars, why
