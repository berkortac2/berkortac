"""Entry-order study on the WALK-FORWARD validation folds (no locked-test data).

For a rules trial, each fold uses the rules found on data before that fold (search artifacts).
  market : taker entry at the next open (research default), cost 0.14% round trip
  limit  : post-only limit at the signal close, valid for one bar; filled only if the next bar
           trades BELOW it (long) / ABOVE it (short); unfilled signals are skipped.
           cost = maker 0.02% + taker exit 0.05% + exit slippage 0.02% = 0.09%
Exits identical (stop first inside a bar, time exit at close[t+H]).

python scripts/13_entry_study.py "rules:d4s0.002:long:top3" 6 nan nan
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from numba import njit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.dataset import final_cutoff, load_dataset, load_universe  # noqa: E402
from tsa.eval.metrics import trade_metrics  # noqa: E402
from tsa.labels import simulate, trade_outcomes  # noqa: E402
from tsa.models import offline as off  # noqa: E402
from tsa.search.deep5m import FEATURE_DIR, SPOT_SEGMENTS  # noqa: E402
from tsa.search.runner import make_folds  # noqa: E402


@njit(cache=True)
def limit_outcomes(o, h, l, c, atr, H, a, b, cost_rt, direction):
    n = o.shape[0]
    net = np.full(n, np.nan)
    ex = np.full(n, -1, np.int64)
    hit = np.full(n, np.nan)
    for t in range(n - 1):
        A = atr[t]
        if np.isnan(A) or A <= 0 or t + H >= n:
            continue
        e = c[t]
        filled = (l[t + 1] < e) if direction > 0 else (h[t + 1] > e)
        if not filled:
            continue
        if direction > 0:
            tp, sl = e + a * A, e - b * A
        else:
            tp, sl = e - a * A, e + b * A
        px = np.nan
        xi = t + H
        for j in range(t + 1, t + H + 1):
            if direction > 0:
                if l[j] <= sl:
                    px = min(sl, o[j]) if j > t + 1 else sl
                    xi = j
                    break
                if h[j] >= tp and j > t + 1:
                    px = max(tp, o[j])
                    xi = j
                    break
            else:
                if h[j] >= sl:
                    px = max(sl, o[j]) if j > t + 1 else sl
                    xi = j
                    break
                if l[j] <= tp and j > t + 1:
                    px = min(tp, o[j])
                    xi = j
                    break
        if np.isnan(px):
            px = c[t + H]
        net[t] = (px / e - 1.0) * direction - cost_rt
        ex[t] = xi
        hit[t] = 1.0 if (c[t + H] - e) * direction > 0 else 0.0
    return net, ex, hit


def run(config: str, H: int, tp, sl):
    uni = load_universe()
    cut = final_cutoff("5m")
    ds = load_dataset("5m", uni["search5m"] + SPOT_SEGMENTS, t_max=cut, feature_dir=FEATURE_DIR)
    folds = make_folds(ds.time, 4)
    perp = np.array(["@spot" not in s for s in ds.symbols])[ds.coin]
    fold_id = np.full(ds.n, -1, np.int8)
    for k, (a, b) in enumerate(folds):
        fold_id[(ds.time >= a) & (ds.time < b) & perp] = k
    _, tag, side, top = config.split(":")
    arts = json.loads((ROOT / "reports" / "search" / "artifacts_5m_deep.json").read_text())[f"rules_H{H}_{tag}"]
    d = 1 if side == "long" else -1
    fi = {n: i for i, n in enumerate(ds.feat_names)}
    sig = np.zeros(ds.n, bool)
    for k in range(4):
        rules = arts[str(k)][str(d)][:int(top[3:])]
        te = np.flatnonzero(fold_id == k)
        m = np.zeros(len(te), bool)
        for r, _ in rules:
            m |= off.rule_mask(ds.X, [(fi[f], op, v) for f, op, v in r], te)
        sig[te] = m
    aa = 1e6 if tp is None else tp
    bb = 1e6 if sl is None else sl
    z = np.zeros(ds.n, bool)
    out = {}
    n_bars = int((fold_id >= 0).sum())
    for name, fn, cost in (("market", trade_outcomes, 0.0014), ("limit", limit_outcomes, 0.0009)):
        net = np.full(ds.n, np.nan); ex = np.full(ds.n, -1, np.int64); hit = np.full(ds.n, np.nan)
        for s, e in zip(ds.seg_start, ds.seg_end):
            n_, x_, h_ = fn(ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e], ds.atr[s:e], H, aa, bb, cost, d)
            net[s:e], hit[s:e] = n_, h_
            ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
        L, S = (sig, z) if d > 0 else (z, sig)
        rows, dirs, nets, hits = simulate(L, S, net, ex, hit, net, ex, hit, ds.seg_start, ds.seg_end)
        m = trade_metrics(dirs, nets, hits, n_bars, 105120, 12)
        m["net_per_coin_day"] = float(nets.sum()) / (n_bars / 288)
        m["fold_avg"] = [round(float(nets[fold_id[rows] == k].mean()), 5) for k in range(4)]
        out[name] = m
        print(f"{name:7s} trades={m['trades']} dir={m.get('dir_hit', 0):.3f} win={m.get('win_rate', 0):.3f} "
              f"avg={m.get('avg_net', 0):.5f} per_coin_day={m['net_per_coin_day']:.6f} folds={m['fold_avg']}", flush=True)
    return out


if __name__ == "__main__":
    cfg, H = sys.argv[1], int(sys.argv[2])
    f = lambda x: None if x == "nan" else float(x)  # noqa: E731
    res = run(cfg, H, f(sys.argv[3]), f(sys.argv[4]))
    p = ROOT / "reports" / "entry_study.json"
    allr = json.loads(p.read_text()) if p.exists() else {}
    allr[f"{cfg}|H{H}|{sys.argv[3]}|{sys.argv[4]}"] = res
    p.write_text(json.dumps(allr, indent=1, default=float))
