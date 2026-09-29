"""Online learners that replay the chart bar by bar.

At bar t they only use feature rows <= t and labels that have *matured*
(the label of bar j is the move from open[j+1] to close[j+H], known once bar
j+H has closed, i.e. j <= t-H). Every coin is replayed independently, exactly
like the Pine version runs on a single TradingView chart.
"""
from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def lorentzian_knn(X, lab, seg_start, seg_end, H, k, window, step):
    """Lorentzian-distance k-NN (jdehorty style, exact neighbours).

    X: (n, F) float32 features, lab: (n,) float64 in {-1, 0, +1} or NaN.
    Candidates: bars j with j <= t-H, t-j <= window and (j - seg) % step == 0.
    Returns score in [-1, 1] = mean label of the k nearest neighbours.
    """
    n, F = X.shape
    out = np.zeros(n)
    bd = np.empty(k)
    bl = np.empty(k)
    for s in range(seg_start.shape[0]):
        a, e = seg_start[s], seg_end[s]
        for t in range(a, e):
            hi = t - H
            if hi - a < step * k:
                continue
            lo = max(a, t - window)
            j0 = lo + ((step - (lo - a) % step) % step)
            m = 0
            for j in range(j0, hi + 1, step):
                if np.isnan(lab[j]):
                    continue
                d = 0.0
                for f in range(F):
                    d += np.log1p(abs(X[t, f] - X[j, f]))
                if m < k:
                    bd[m] = d
                    bl[m] = lab[j]
                    m += 1
                else:
                    # replace current worst
                    w = 0
                    for q in range(1, k):
                        if bd[q] > bd[w]:
                            w = q
                    if d < bd[w]:
                        bd[w] = d
                        bl[w] = lab[j]
            if m > 0:
                sc = 0.0
                for q in range(m):
                    sc += bl[q]
                out[t] = sc / m
    return out


@njit(cache=True)
def online_logit(Xs, y, seg_start, seg_end, H, w0, b0, lr, l2):
    """Per-coin SGD logistic regression, initialised from a pooled prior (w0, b0).

    Xs: standardised features (n, F); y: (n,) 1/0 or NaN (label of bar j,
    usable from bar j+H on). Score = 2*p-1 with p predicted *before* the
    update at each bar."""
    n, F = Xs.shape
    out = np.zeros(n)
    w = np.empty(F)
    for s in range(seg_start.shape[0]):
        a, e = seg_start[s], seg_end[s]
        for f in range(F):
            w[f] = w0[f]
        b = b0
        for t in range(a, e):
            z = b
            for f in range(F):
                z += w[f] * Xs[t, f]
            p = 1.0 / (1.0 + np.exp(-z))
            out[t] = 2.0 * p - 1.0
            j = t - H
            if j >= a and not np.isnan(y[j]):
                zj = b
                for f in range(F):
                    zj += w[f] * Xs[j, f]
                pj = 1.0 / (1.0 + np.exp(-zj))
                g = y[j] - pj
                for f in range(F):
                    w[f] += lr * (g * Xs[j, f] - l2 * (w[f] - w0[f]))
                b += lr * g
    return out
