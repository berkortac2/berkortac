"""Parity: the exported Pine parameters reproduce the locked-test trades.

For every timeframe of the winning family, the score is recomputed from
reports/final_models.json with the Pine mirror, turned into trades with the
same one-position simulator, and compared with the trades the research model
produced in the locked test (reports/final_trades.parquet).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.dataset import final_cutoff, load_cfg, load_dataset, load_universe, tf_ms  # noqa: E402
from tsa.export import pine_mirror  # noqa: E402
from tsa.final import ONLINE_WARM, evaluate_frozen  # noqa: E402


class _Mirror:
    def __init__(self, m):
        self.m = m
        self.H = int(m["H"])
        self.tp = m["tp_atr"]
        self.sl = m["sl_atr"]

    def signals(self, ds):
        F = pd.DataFrame(ds.X, columns=ds.feat_names)
        L, S = pine_mirror.signals(self.m, F)
        return L, S, None


def main():
    cfg = load_cfg()
    uni = load_universe()
    cost = 2 * (cfg["fee_per_side"] + cfg["slippage_per_side"])
    fam = json.loads((ROOT / "reports" / "winner.json").read_text())["family"]
    models = json.loads((ROOT / "reports" / "final_models.json").read_text())[fam]
    ref = pd.read_parquet(ROOT / "reports" / "final_trades.parquet")
    rows = []
    for tf, m in models.items():
        if fam in ("knn", "online_logit"):
            print(f"{tf}: stateful family, parity is structural (same replay code)")
            continue
        cut = final_cutoff(tf)
        ds = load_dataset(tf, uni["train"], t_min=cut - ONLINE_WARM * tf_ms(tf))
        _, trades = evaluate_frozen(_Mirror(m), ds, cut, cost, tf)
        r = ref[(ref.tf == tf) & (ref.family == fam) & (ref.group == "train_coins")]
        a = set(zip(trades.time, trades.coin, trades.dir))
        b = set(zip(r.time, r.coin, r.dir))
        match = len(a & b) / max(len(a | b), 1)
        rows.append({"tf": tf, "mirror_trades": len(a), "research_trades": len(b), "jaccard": match})
        print(rows[-1], flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "reports" / "pine_parity.csv", index=False)
    print(out.to_string())


if __name__ == "__main__":
    main()
