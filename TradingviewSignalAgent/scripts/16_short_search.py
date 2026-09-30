"""SAT (short) search on 5m, round 3.

New vs round 2: the rule search optimises the SHORT triple-barrier outcome itself (TP/SL/H),
not the time-exit return, so quick "pump fades" that only work with a take-profit can be found.
Plus one fixed hypothesis: the mirror image of the live AL rule (blow-off top).
Walk-forward folds identical to the deep search; cost 0.14% round trip.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.eval.metrics import trade_metrics  # noqa: E402
from tsa.labels import simulate, trade_outcomes  # noqa: E402
from tsa.models import offline as off  # noqa: E402
from tsa.search.deep5m import BEAM, RULE_QS, DeepSearch5m  # noqa: E402
from tsa.search.runner import DISCRETE  # noqa: E402

CONFIGS = [(1.0, 1.0, 12), (1.5, 1.0, 12), (1.0, 1.5, 24), (2.0, 2.0, 48), (0.75, 1.5, 6)]
VARIANTS = [dict(depth=3, min_support=0.002), dict(depth=3, min_support=0.01)]


def short_net(ds, a, b, H, cost):
    net = np.full(ds.n, np.nan)
    ex = np.full(ds.n, -1, np.int64)
    hit = np.full(ds.n, np.nan)
    for s, e in zip(ds.seg_start, ds.seg_end):
        n_, x_, h_ = trade_outcomes(ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e], ds.atr[s:e], H, a, b, cost, -1)
        net[s:e], hit[s:e] = n_, h_
        ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
    return net, ex, hit


def evaluate(se, S, out, name, extra):
    ds = se.ds
    S = S & se.test_mask
    z = np.zeros(ds.n, bool)
    net, ex, hit = out
    rows, dirs, nets, hits = simulate(z, S, net, ex, hit, net, ex, hit, ds.seg_start, ds.seg_end)
    m = trade_metrics(dirs, nets, hits, se.n_test_bars, 105120, se.n_test_coins)
    fm = [float(nets[se.fold_id[rows] == k].mean()) if (se.fold_id[rows] == k).sum() >= 10 else float("nan")
          for k in range(4)]
    pos = float(np.nansum(np.array(fm) > 0) / 4)
    row = {"config": name, **extra, "trades": m["trades"], "dir_hit": m.get("dir_hit"), "win_rate": m.get("win_rate"),
           "avg_net": m.get("avg_net"), "tstat": m.get("tstat"), "pos_folds": pos,
           "trades_per_coin_day": m["trades"] / (se.n_test_bars / 288),
           **{f"fold{k}": v for k, v in enumerate(fm)}}
    print({k: (round(v, 5) if isinstance(v, float) else v) for k, v in row.items()}, flush=True)
    return row


def main():
    se = DeepSearch5m(ROOT / "reports" / "search", log=lambda m: print(m, flush=True), horizons=[12])
    ds = se.ds
    fi = {n: i for i, n in enumerate(ds.feat_names)}
    rows, arts = [], {}
    # fixed hypothesis: mirror of the live AL rule
    v1 = json.loads((ROOT / "bot" / "model" / "model_5m.json").read_text())["long"]["rules"][0]
    flip = {"<=": ">=", ">=": "<="}
    mirror = [(fi[f], flip[op], -v) for f, op, v in v1]
    Sm = off.rule_mask(ds.X, mirror)
    for a, b, H in CONFIGS + [(1e6, 1e6, 24)]:
        out = short_net(ds, a, b, H, se.cost)
        rows.append(evaluate(se, Sm, out, "mirror_of_v1", {"tp": a, "sl": b, "H": H}))
        # barrier-target rule search
        if a > 1e5:
            continue
        tgt = out[0]
        for v in VARIANTS:
            S1 = np.zeros(ds.n, bool)
            S3 = np.zeros(ds.n, bool)
            found = {}
            for k in range(4):
                tr = se.train_idx(k, H)
                prims = off.make_primitives(ds.X, ds.feat_names, tr, DISCRETE, qs=RULE_QS)
                rules = off.beam_search(ds.X, tgt, tr, prims, H, depth=v["depth"], beam=BEAM,
                                        min_support=v["min_support"], max_rows=400_000)
                found[k] = [([(ds.feat_names[j], op, val) for j, op, val in r], s) for r, s in rules]
                te = se.test_idx(k)
                if rules:
                    ms = [off.rule_mask(ds.X, r, te) for r, _ in rules[:3]]
                    S1[te] = ms[0]
                    S3[te] = np.logical_or.reduce(ms)
            tag = f"tp{a}_sl{b}_H{H}_d{v['depth']}s{v['min_support']}"
            arts[tag] = found
            for top, S in ((1, S1), (3, S3)):
                if S.any():
                    rows.append(evaluate(se, S, out, f"barrier_rules:{tag}:top{top}", {"tp": a, "sl": b, "H": H}))
            pd.DataFrame(rows).to_csv(ROOT / "reports" / "short_search_5m.csv", index=False)
            (ROOT / "reports" / "short_search_5m_rules.json").write_text(json.dumps(arts, indent=1, default=str))
        del out, tgt
    pd.DataFrame(rows).to_csv(ROOT / "reports" / "short_search_5m.csv", index=False)


if __name__ == "__main__":
    main()
