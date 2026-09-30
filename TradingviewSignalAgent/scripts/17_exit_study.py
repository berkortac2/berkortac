"""When to close (take profit / exit) the AL positions of the live 5m model.

Every policy = max hold Hmax + ATR take-profit + ATR stop + trailing stop + indicator "SAT/ÇIK" exit,
always with the bot's 8% emergency stop. Entry = next open after an AL signal, one position per coin.

Periods (the AL rule was fitted on 2023-09..2026-02 of 10 coins):
  early  : perps 2020-01..2023-08 + spot 2017-08..2019-12  (never seen by the AL rule)
  mid    : 12 perps 2023-09..2026-02-16
  locked : 36 coins 2026-02-16..2026-09-28
  hold25 : 10 never-used coins 2025-01..2026-02-16
Pre-registered choice: the policy with the largest min(t_early, t_mid); locked/hold25 only confirm.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.dataset import final_cutoff, load_dataset, load_universe  # noqa: E402
from tsa.exits import EXIT_FEATS, sim  # noqa: E402,F401
from tsa.models import offline as off  # noqa: E402
from tsa.search.deep5m import FEATURE_DIR, SPOT_SEGMENTS  # noqa: E402

COST = 0.0014
EMERG = 0.08
IND = {"none": (-1, [0.0]), "rsi": (0, [0.0, 0.2, 0.4]), "ema50": (1, [0.0, 1.0]), "macd_hist": (2, [0.0]),
       "stoch": (3, [0.6, 0.9]), "cci": (4, [1.0]), "wt": (5, [0.0, 1.0]), "chan20": (6, [0.8]), "di": (7, [0.0, 0.2])}
GRID = dict(Hmax=[12, 24, 48, 96], tp=[0.0, 1.0, 1.5, 2.0, 3.0, 5.0], sl=[0.0, 1.5, 3.0],
            trail=[(0.0, 0.0), (1.0, 1.0), (2.0, 1.5)])
SPLIT = 1693526400000  # 2023-09-01


def load(name, uni, cut, v1):
    feats = sorted({f for r in v1 for f, _, _ in r} | set(EXIT_FEATS))
    if name == "early":
        a = load_dataset("5m", uni["search5m"], t_max=SPLIT, features=feats, feature_dir=FEATURE_DIR)
        b = load_dataset("5m", SPOT_SEGMENTS, features=feats, feature_dir=FEATURE_DIR)
        return [a, b]
    if name == "mid":
        return [load_dataset("5m", uni["search5m"], t_min=SPLIT, t_max=cut, features=feats, feature_dir=FEATURE_DIR)]
    if name == "locked":
        return [load_dataset("5m", uni["train"] + uni["unseen"] + uni["holdout2"], t_min=cut, features=feats,
                             feature_dir=FEATURE_DIR)]
    return [load_dataset("5m", uni["holdout2"], t_max=cut, features=feats, feature_dir=FEATURE_DIR)]


def main():
    uni = load_universe()
    cut = final_cutoff("5m")
    v1 = json.loads((ROOT / "bot" / "model" / "model_5m.json").read_text())["long"]["rules"]
    policies = []
    for Hm, tp, sl, tr in itertools.product(GRID["Hmax"], GRID["tp"], GRID["sl"], GRID["trail"]):
        for ind, (iid, thrs) in IND.items():
            for th in thrs:
                policies.append((Hm, tp, sl, tr[0], tr[1], ind, iid, th))
    res = []
    for period in ("early", "mid", "locked", "hold25"):
        parts = load(period, uni, cut, v1)
        pre = []
        for ds in parts:
            fi = {n: i for i, n in enumerate(ds.feat_names)}
            sig = np.zeros(ds.n, bool)
            for r in v1:
                sig |= off.rule_mask(ds.X, [(fi[f], op, v) for f, op, v in r])
            F = np.ascontiguousarray(ds.X[:, [fi[f] for f in EXIT_FEATS]].astype(np.float64))
            coin_days = ds.n / 288
            pre.append((ds, sig, F, coin_days))
        print(period, "signals", sum(int(p[1].sum()) for p in pre), flush=True)
        for pol in policies:
            Hm, tp, sl, ta, td, ind, iid, th = pol
            nets, bars, whys = [], [], []
            for ds, sig, F, _ in pre:
                n_, b_, w_ = sim(ds.o, ds.h, ds.l, ds.c, ds.atr, sig, F, ds.seg_start, ds.seg_end,
                                 Hm, tp, sl, EMERG, ta, td, iid, th, COST)
                nets.append(n_); bars.append(b_); whys.append(w_)
            nets, bars, whys = np.concatenate(nets), np.concatenate(bars), np.concatenate(whys)
            n = len(nets)
            cd = sum(p[3] for p in pre)
            res.append({"period": period, "Hmax": Hm, "tp": tp, "sl": sl, "trail": f"{ta}/{td}", "ind": ind, "thr": th,
                        "trades": n, "avg_net": float(nets.mean()) if n else np.nan,
                        "tstat": float(nets.mean() / (nets.std(ddof=1) + 1e-12) * np.sqrt(n)) if n > 2 else np.nan,
                        "win": float((nets > 0).mean()) if n else np.nan, "bars": float(bars.mean()) if n else np.nan,
                        "worst": float(nets.min()) if n else np.nan, "net_per_coin_day": float(nets.sum() / cd),
                        "pct_sl": float((whys == 1).mean()) if n else np.nan, "pct_tp": float((whys == 2).mean()) if n else np.nan,
                        "pct_ind": float((whys == 3).mean()) if n else np.nan})
        del pre, parts
    df = pd.DataFrame(res)
    df.to_parquet(ROOT / "reports" / "exit_study_5m.parquet", index=False)
    key = ["Hmax", "tp", "sl", "trail", "ind", "thr"]
    w = df.pivot_table(index=key, columns="period", values=["tstat", "avg_net", "trades", "win", "bars",
                                                            "net_per_coin_day", "worst"])
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w["robust"] = w[["tstat_early", "tstat_mid"]].min(axis=1)
    w = w.sort_values("robust", ascending=False).reset_index()
    w.to_csv(ROOT / "reports" / "exit_study_5m_summary.csv", index=False)
    base = w[(w.Hmax == 24) & (w.tp == 0) & (w.sl == 0) & (w.trail == "0.0/0.0") & (w.ind == "none")]
    cols = key + [f"{m}_{p}" for p in ("early", "mid", "locked", "hold25") for m in ("avg_net", "tstat", "trades")]
    print("BASELINE (v1: 24 bars + 8% stop)\n", base[cols].to_string(index=False))
    print("TOP by min(t_early, t_mid)\n", w[cols].head(15).to_string(index=False))


if __name__ == "__main__":
    main()
