"""Indicator formulas (Pine semantics) and trade-outcome labelling."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.indicators import pine_ta as ta  # noqa: E402
from tsa.labels import simulate, trade_outcomes  # noqa: E402


def _ref_rma(x, n):
    """Straight transcription of Pine's documented ta.rma."""
    out, s = [], np.nan
    for i in range(len(x)):
        if np.isnan(s):
            w = x[max(0, i - n + 1): i + 1]
            s = np.mean(w) if len(w) == n and not np.isnan(w).any() else np.nan
        else:
            s = (x[i] + (n - 1) * s) / n
        out.append(s)
    return np.array(out)


def test_rma_matches_pine_definition():
    x = np.random.default_rng(0).normal(size=300)
    np.testing.assert_allclose(ta.rma(x, 14), _ref_rma(x, 14), equal_nan=True)


def test_rsi_bounds_and_known_case():
    c = np.array([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03,
                  45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64])
    r = ta.rsi(c, 14)
    assert np.isnan(r[:14]).all()
    # Wilder's classic worked example (seeded with the SMA of the first 14 changes) -> 70.46 at bar 14
    assert abs(r[14] - 70.46) < 0.05
    assert (r[14:] >= 0).all() and (r[14:] <= 100).all()


def test_ema_seed_and_recursion():
    x = np.arange(1, 51, dtype=float)
    e = ta.ema(x, 10)
    assert np.isnan(e[:9]).all()
    assert e[9] == np.mean(x[:10])
    a = 2 / 11
    assert abs(e[10] - (a * x[10] + (1 - a) * e[9])) < 1e-12


def test_correlation_matches_numpy():
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=100), rng.normal(size=100)
    c = ta.correlation(a, b, 20)
    assert abs(c[50] - np.corrcoef(a[31:51], b[31:51])[0, 1]) < 1e-12


def test_pivots_are_delayed():
    x = np.array([1, 2, 3, 9, 3, 2, 1, 0, 1, 2], float)
    ph = ta.pivothigh(x, 3, 2)
    assert np.isnan(ph[:5]).all() and ph[5] == 9  # pivot at bar 3 known only at bar 5


def test_trade_outcome_tp_sl_time():
    n = 10
    o = np.full(n, 100.0); c = o.copy(); h = o + 0.5; l = o - 0.5
    atr = np.full(n, 1.0)
    h[3] = 102.5  # long TP (2 ATR) touched on bar 3
    net, ex, hit = trade_outcomes(o, h, l, c, atr, 5, 2.0, 1.0, 0.0, 1)
    assert ex[0] == 3 and abs(net[0] - 0.02) < 1e-12
    l2 = l.copy(); l2[2] = 98.5; h2 = h.copy(); h2[2] = 102.5  # both in the same bar -> SL first
    net, ex, _ = trade_outcomes(o, h2, l2, c, atr, 5, 2.0, 1.0, 0.0, 1)
    assert ex[0] == 2 and abs(net[0] + 0.01) < 1e-12
    net, ex, _ = trade_outcomes(o, h, l, c, atr, 3, 5.0, 5.0, 0.001, -1)  # time exit, short, with cost
    assert ex[0] == 3 and abs(net[0] + 0.001) < 1e-12


def test_simulate_one_position_at_a_time():
    n = 20
    sigL = np.zeros(n, bool); sigL[[1, 2, 3, 8]] = True
    sigS = np.zeros(n, bool)
    net = np.full(n, 0.01); ex = np.arange(n) + 4; hit = np.ones(n)
    ex[ex >= n] = n + 5
    rows, dirs, nets, hits = simulate(sigL, sigS, net, ex, hit, net, ex, hit,
                                      np.array([0]), np.array([n]))
    assert rows.tolist() == [1, 8]  # 2 and 3 are inside the first trade (exit at bar 5)
