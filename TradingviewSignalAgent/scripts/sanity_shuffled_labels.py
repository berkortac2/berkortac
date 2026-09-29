"""Leakage sanity check on the search period (never touches the locked test).

The same logistic pipeline is trained once on the real labels and once on
labels shuffled *within each coin*; with no leakage the shuffled model must
fall to the random-signal baseline.

python scripts/sanity_shuffled_labels.py --tf 4h --H 6
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.models import offline as off  # noqa: E402
from tsa.search.runner import Search, model_feature_sets  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="4h")
    ap.add_argument("--H", type=int, default=6)
    args = ap.parse_args()
    out = ROOT / "reports" / "sanity"
    s = Search(args.tf, out, horizons=[args.H])
    ds = s.ds
    sets = model_feature_sets(s.names)
    fi = [s.fidx[f] for f in sets["plus_corr2"]]
    y_real = off.binary_target(ds.fwd_ret_atr(args.H))
    rng = np.random.default_rng(7)
    y_shuf = y_real.copy()
    for a, b in zip(ds.seg_start, ds.seg_end):
        y_shuf[a:b] = rng.permutation(y_shuf[a:b])
    for tag, y in (("real", y_real), ("shuffled", y_shuf)):
        by_fold = {}
        for k in range(len(s.folds)):
            tr = s.train_idx(k, args.H)
            m = off.Logit(fi, C=0.05).fit(ds.X, y, tr)
            smp = tr[np.random.default_rng(k).choice(len(tr), min(len(tr), 100_000), replace=False)]
            te = s.test_idx(k)
            by_fold[k] = (m.score(ds.X, smp), te, m.score(ds.X, te))
        s.thresholds_eval(by_fold, args.H, "sanity", f"logit_plus_corr2_{tag}")
    s.run_baselines(args.H)
    df = pd.DataFrame(s.trials)
    df = df[df.tp_atr.isna()]  # time-exit only for a clean comparison
    cols = ["config", "q", "trades", "dir_hit", "win_rate", "avg_net", "tstat"]
    res = df[cols].sort_values(["config", "q"])
    print(res.to_string())
    res.to_csv(out / f"shuffled_labels_{args.tf}_H{args.H}.csv", index=False)


if __name__ == "__main__":
    main()
