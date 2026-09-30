"""Re-evaluate the live 5m model exactly as the bot trades it: the model's own exits PLUS the
bot's exchange-side emergency stop (default 8% from entry) for time-exit-only trades.
Writes reports/bot_trades_5m.parquet (model 'v1' = research, 'v1_stop8' = bot) for 14_portfolio_sim."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.dataset import final_cutoff, load_dataset, load_universe  # noqa: E402
from tsa.labels import simulate, trade_outcomes  # noqa: E402
from tsa.models import offline as off  # noqa: E402
from tsa.search.deep5m import FEATURE_DIR  # noqa: E402

COST = 0.0014


def run(ds, model, t0, t1, stop_pct):
    fi = {n: i for i, n in enumerate(ds.feat_names)}
    live = (ds.time >= t0) & ((ds.time < t1) if t1 else True)
    L = np.zeros(ds.n, bool)
    for r in model["long"]["rules"]:
        L |= off.rule_mask(ds.X, [(fi[f], op, v) for f, op, v in r])
    L &= live
    H = model["long"]["H"]
    net = np.full(ds.n, np.nan); ex = np.full(ds.n, -1, np.int64); hit = np.full(ds.n, np.nan)
    for s, e in zip(ds.seg_start, ds.seg_end):
        o, h, l, c = ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e]
        if stop_pct:
            A = np.append(o[1:], np.nan) * stop_pct      # stop distance = stop_pct * entry price
            n_, x_, h_ = trade_outcomes(o, h, l, c, A, H, 1e9, 1.0, COST, 1)
        else:
            n_, x_, h_ = trade_outcomes(o, h, l, c, ds.atr[s:e], H, 1e6, 1e6, COST, 1)
        net[s:e], hit[s:e] = n_, h_
        ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
    z = np.zeros(ds.n, bool)
    rows, dirs, nets, hits = simulate(L, z, net, ex, hit, net, ex, hit, ds.seg_start, ds.seg_end)
    return pd.DataFrame({"time": ds.time[rows], "coin": [ds.symbols[i] for i in ds.coin[rows]], "dir": dirs,
                         "net": nets, "hit": hits})


def main():
    model = json.loads((ROOT / "bot" / "model" / "model_5m.json").read_text())
    uni = load_universe()
    cut = final_cutoff("5m")
    out = []
    for group, syms, t0, t1 in (("locked36", uni["train"] + uni["unseen"] + uni["holdout2"], cut, None),
                                ("holdout10_pre", uni["holdout2"], 0, cut)):
        ds = load_dataset("5m", syms, t_min=(t0 - 400 * 300_000) if t0 else None, t_max=t1, feature_dir=FEATURE_DIR)
        for name, sp in (("v1", None), ("v1_stop8", 0.08)):
            tr = run(ds, model, t0, t1, sp)
            tr["group"], tr["model"] = group, name
            out.append(tr)
            print(group, name, len(tr), "avg", round(tr.net.mean(), 5), "worst", round(tr.net.min(), 4),
                  "sum", round(tr.net.sum(), 3), flush=True)
        del ds
    pd.concat(out).to_parquet(ROOT / "reports" / "bot_trades_5m.parquet", index=False)


if __name__ == "__main__":
    main()
