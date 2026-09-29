"""The research must never peek into the future.

1. Truncation test: every feature value at bar t is identical whether it is
   computed on the full history or on history cut at t (the "replay" view).
2. Future-shuffle test: scrambling bars after t does not change features <= t.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.features.registry import compute_features  # noqa: E402


def _synthetic(n=1500, seed=0, step_ms=3_600_000):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.01, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate([[100], c[:-1]])
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.005, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.005, n))
    v = rng.lognormal(10, 0.5, n)
    t = 1_600_000_000_000 + np.arange(n) * step_ms
    return pd.DataFrame({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})


def _htf(df, k=4):
    g = np.arange(len(df)) // k
    return df.groupby(g).agg(open_time=("open_time", "first"), open=("open", "first"), high=("high", "max"),
                             low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index(drop=True)


@pytest.mark.parametrize("t", [400, 777, 1203, 1499])
def test_truncation(t):
    df = _synthetic()
    htf_full = _htf(df)
    full = compute_features(df, "1h", htf_full)
    cut = df.iloc[: t + 1]
    # the HTF feed also only knows HTF bars that have started by t
    htf_cut = htf_full[htf_full["open_time"] <= cut["open_time"].iloc[-1]].copy()
    # the HTF bar still forming at t must not leak its final values: rebuild it from cut data
    part = _htf(cut)
    htf_cut.iloc[-1] = part.iloc[-1]
    trunc = compute_features(cut, "1h", htf_cut)
    a = full.iloc[t].to_numpy(dtype=float)
    b = trunc.iloc[t].to_numpy(dtype=float)
    bad = [c for c, x, y in zip(full.columns, a, b) if not (np.isclose(x, y, rtol=1e-9, atol=1e-12) or (np.isnan(x) and np.isnan(y)))]
    assert not bad, f"non-causal features at t={t}: {bad}"


def test_future_shuffle():
    df = _synthetic(seed=1)
    t = 1000
    df2 = df.copy()
    rng = np.random.default_rng(5)
    for col in ("open", "high", "low", "close", "volume"):
        df2.loc[t + 1:, col] = df2.loc[t + 1:, col].to_numpy()[rng.permutation(len(df) - t - 1)]
    df2["high"] = df2[["open", "high", "low", "close"]].max(axis=1)
    df2["low"] = df2[["open", "high", "low", "close"]].min(axis=1)
    a = compute_features(df, "1h").iloc[: t + 1]
    b = compute_features(df2, "1h").iloc[: t + 1]
    pd.testing.assert_frame_equal(a, b)
