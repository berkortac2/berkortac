"""Compute features for every coin/timeframe and store float32 parquet.

python scripts/02_build_features.py --tfs 1h,4h
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.features.registry import HTF_OF, WARMUP, compute_features  # noqa: E402
from tsa.indicators import pine_ta as ta  # noqa: E402


def build_one(args):
    sym, tf, raw_dir, out_dir = args
    p = Path(raw_dir) / f"{sym}_{tf}.parquet"
    out = Path(out_dir) / tf / f"{sym}.parquet"
    if out.exists() and out.stat().st_mtime > p.stat().st_mtime:
        return sym, tf, "cached", 0
    t0 = time.time()
    df = pd.read_parquet(p)
    htf_name = HTF_OF[tf]
    htf = None
    if htf_name:
        hp = Path(raw_dir) / f"{sym}_{htf_name}.parquet"
        if hp.exists():
            htf = pd.read_parquet(hp)
    f = compute_features(df, tf, htf)
    f = f.astype(np.float32)
    base = pd.DataFrame({
        "open_time": df["open_time"].to_numpy(),
        "open": df["open"].to_numpy(), "high": df["high"].to_numpy(),
        "low": df["low"].to_numpy(), "close": df["close"].to_numpy(),
        "atr": ta.atr(df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy(), 14),
    })
    res = pd.concat([base, f], axis=1).iloc[(52 if tf == "1w" else WARMUP):]
    if htf_name and "htf_rsi" in res:
        res = res[res["htf_rsi"].notna()]
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_parquet(out, index=False)
    return sym, tf, len(res), round(time.time() - t0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tfs", default="1h,4h,1d,1w,30m,15m,5m,1m")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--symbols", default="", help="comma list; default = train + unseen")
    ap.add_argument("--out", default="", help="feature dir override (e.g. data/features_ext)")
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())
    uni = json.loads((ROOT / "config" / "universe.json").read_text())
    syms = args.symbols.split(",") if args.symbols else uni["train"] + uni["unseen"]
    fdir = ROOT / (args.out or cfg["feature_dir"])
    jobs = [(s, tf, str(ROOT / cfg["data_dir"]), str(fdir))
            for tf in args.tfs.split(",") for s in syms
            if (ROOT / cfg["data_dir"] / f"{s}_{tf}.parquet").exists()]
    with ProcessPoolExecutor(args.workers) as ex:
        for r in ex.map(build_one, jobs):
            print(r, flush=True)


if __name__ == "__main__":
    main()
