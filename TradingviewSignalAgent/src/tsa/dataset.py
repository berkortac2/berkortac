"""Load per-coin feature files of one timeframe into contiguous arrays."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .data.binance_um import TF_MINUTES

ROOT = Path(__file__).resolve().parents[2]
BASE_COLS = ["open_time", "open", "high", "low", "close", "atr"]


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())


def load_universe() -> dict:
    return json.loads((ROOT / "config" / "universe.json").read_text())


def tf_ms(tf: str) -> int:
    return TF_MINUTES[tf] * 60_000


def final_cutoff(tf: str) -> int:
    """Debug mode (env TSA_DEBUG_FINAL=1) moves the boundary 15% earlier so the
    final-test code can be exercised without ever touching the locked period.

    open_time (ms) where the locked final-test period starts for this TF.

    Fixed once and stored in config/splits.json so every script uses the same
    boundary."""
    p = ROOT / "config" / "splits.json"
    splits = json.loads(p.read_text()) if p.exists() else {}
    if tf in splits:
        cut = int(splits[tf])
        if os.environ.get("TSA_DEBUG_FINAL") == "1":
            lo = int(splits.get(f"{tf}_start", cut - 5 * 365 * 86_400_000))
            cut -= int(0.15 * (cut - lo))
            cut -= cut % tf_ms(tf)
        return cut
    cfg = load_cfg()
    uni = load_universe()
    fdir = ROOT / cfg["feature_dir"] / tf
    t0, t1 = [], []
    for s in uni["train"]:
        fp = fdir / f"{s}.parquet"
        if fp.exists():
            t = pd.read_parquet(fp, columns=["open_time"])["open_time"]
            t0.append(t.iloc[0]); t1.append(t.iloc[-1])
    lo, hi = int(np.median(t0)), int(max(t1))
    cut = lo + int((hi - lo) * (1 - cfg["final_test_frac"]))
    cut -= cut % tf_ms(tf)
    splits[tf] = cut
    splits[f"{tf}_start"] = lo
    p.write_text(json.dumps(splits, indent=2))
    return cut


@dataclass
class Dataset:
    tf: str
    symbols: list
    feat_names: list
    X: np.ndarray            # float32 (n, F)
    time: np.ndarray         # int64 open_time ms
    coin: np.ndarray         # int16 index into symbols
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    atr: np.ndarray
    seg_start: np.ndarray
    seg_end: np.ndarray
    cache: dict = field(default_factory=dict)

    @property
    def n(self):
        return self.X.shape[0]

    def col(self, name: str) -> np.ndarray:
        return self.X[:, self.feat_names.index(name)]

    def fwd_ret_atr(self, H: int) -> np.ndarray:
        """(close[t+H] - open[t+1]) / atr[t], NaN when it crosses a coin boundary."""
        key = ("fwd", H)
        if key not in self.cache:
            out = np.full(self.n, np.nan)
            for s, e in zip(self.seg_start, self.seg_end):
                if e - s > H + 1:
                    out[s:e - H] = (self.c[s + H:e] - self.o[s + 1:e - H + 1]) / self.atr[s:e - H]
            self.cache[key] = out
        return self.cache[key]

    def fwd_net(self, H: int, cost_rt: float) -> np.ndarray:
        """Net long return (fraction) of a time-exit-only trade, per bar."""
        key = ("fwdnet", H, cost_rt)
        if key not in self.cache:
            out = np.full(self.n, np.nan)
            for s, e in zip(self.seg_start, self.seg_end):
                if e - s > H + 1:
                    out[s:e - H] = self.c[s + H:e] / self.o[s + 1:e - H + 1] - 1.0
            self.cache[key] = out
        return self.cache[key]


def load_dataset(tf: str, symbols: list, t_min: int | None = None, t_max: int | None = None,
                 features: list | None = None, stride: int = 1, feature_dir: str | None = None) -> Dataset:
    cfg = load_cfg()
    fdir = ROOT / (feature_dir or cfg["feature_dir"]) / tf
    parts, syms = [], []
    feat_names = None
    for s in symbols:
        fp = fdir / f"{s}.parquet"
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)
        if t_min is not None:
            df = df[df["open_time"] >= t_min]
        if t_max is not None:
            df = df[df["open_time"] < t_max]
        if len(df) < (40 if tf == "1w" else 500):
            continue
        if feat_names is None:
            feat_names = features or [c for c in df.columns if c not in BASE_COLS]
        parts.append(df)
        syms.append(s)
    n = sum(len(p) for p in parts)
    F = len(feat_names)
    X = np.empty((n, F), np.float32)
    arrs = {k: np.empty(n) for k in ("o", "h", "l", "c", "atr")}
    time = np.empty(n, np.int64)
    coin = np.empty(n, np.int16)
    seg_s, seg_e = [], []
    k = 0
    for i, df in enumerate(parts):
        m = len(df)
        X[k:k + m] = np.nan_to_num(df[feat_names].to_numpy(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        for a, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("atr", "atr")):
            arrs[a][k:k + m] = df[col].to_numpy()
        time[k:k + m] = df["open_time"].to_numpy()
        coin[k:k + m] = i
        seg_s.append(k); seg_e.append(k + m)
        k += m
    return Dataset(tf, syms, feat_names, X, time, coin, arrs["o"], arrs["h"], arrs["l"], arrs["c"],
                   arrs["atr"], np.array(seg_s, np.int64), np.array(seg_e, np.int64))
