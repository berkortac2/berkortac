"""SAT hypothesis: mirror image of the live AL rule ("blow-off top" in high volatility), corrected so that only
DIRECTIONAL features are flipped (atr_pct stays >= threshold). Evaluated with a small exit grid on the same
four periods as the exit study; selection on early+mid only."""
from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.dataset import final_cutoff, load_universe  # noqa: E402
from tsa.models import offline as off  # noqa: E402

spec = importlib.util.spec_from_file_location("es", ROOT / "scripts" / "17_exit_study.py")
es = importlib.util.module_from_spec(spec)
spec.loader.exec_module(es)
DIRECTIONAL = {"di", "htf_hist_atr", "rsi", "rsi7", "hist_atr", "macd_atr", "sig_atr", "cci", "wt", "stoch", "ret5_atr",
               "ema50_dist", "chan20", "htf_rsi", "htf_ema50_dist", "htf_chan20"}
FLIP = {"<=": ">=", ">=": "<=", "<": ">", ">": "<"}


@njit(cache=True)
def sim_short(o, h, l, c, atr, sig, xs, seg_s, seg_e, Hmax, tp, sl, emerg, cost):
    out = np.empty(o.shape[0])
    k = 0
    for s in range(seg_s.shape[0]):
        t = seg_s[s]
        e = seg_e[s]
        while t < e - 1:
            if not sig[t] or np.isnan(atr[t]) or atr[t] <= 0:
                t += 1
                continue
            A = atr[t]
            en = o[t + 1]
            tpP = en - tp * A if tp > 0 else -1e18
            stop = en * (1 + emerg)
            if sl > 0:
                stop = min(stop, en + sl * A)
            px = np.nan
            last = min(t + Hmax, e - 1)
            xi = last
            for j in range(t + 1, last + 1):
                if h[j] >= stop:
                    px = stop if j == t + 1 else max(stop, o[j])
                    xi = j
                    break
                if l[j] <= tpP:
                    px = tpP if j == t + 1 else min(tpP, o[j])
                    xi = j
                    break
                if xs[j]:
                    px = c[j]
                    xi = j
                    break
            if np.isnan(px):
                px = c[xi]
            out[k] = 1.0 - px / en - cost
            k += 1
            t = xi
    return out[:k]


def main():
    uni = load_universe()
    cut = final_cutoff("5m")
    v1 = json.loads((ROOT / "bot" / "model" / "model_5m.json").read_text())["long"]["rules"]
    mirror = [[(f, FLIP[op] if f in DIRECTIONAL else op, -v if f in DIRECTIONAL else v) for f, op, v in r] for r in v1]
    print("mirror rule:", mirror)
    exits = {"none": None, "wt<=-1": ("wt", -1.0), "rsi<=-0.2": ("rsi", -0.2), "stoch<=-0.6": ("stoch", -0.6)}
    grid = list(itertools.product([6, 24, 96], [0.0, 1.0, 2.0], [0.0, 1.5, 3.0], list(exits)))
    rows = []
    for period in ("early", "mid", "locked", "hold25"):
        parts = es.load(period, uni, cut, v1 + [[("wt", ">=", 0), ("rsi", ">=", 0), ("stoch", ">=", 0)]])
        pre = []
        for ds in parts:
            fi = {n: i for i, n in enumerate(ds.feat_names)}
            sig = np.zeros(ds.n, bool)
            for r in mirror:
                sig |= off.rule_mask(ds.X, [(fi[f], op, v) for f, op, v in r])
            xcols = {k: (ds.X[:, fi[v[0]]] <= v[1]) if v else np.zeros(ds.n, bool) for k, v in exits.items()}
            pre.append((ds, sig, xcols))
        print(period, "signals", sum(int(p[1].sum()) for p in pre), flush=True)
        for H, tp, sl, ex in grid:
            nets = np.concatenate([sim_short(ds.o, ds.h, ds.l, ds.c, ds.atr, sig, xc[ex], ds.seg_start, ds.seg_end,
                                             H, tp, sl, 0.08, 0.0014) for ds, sig, xc in pre])
            n = len(nets)
            rows.append({"period": period, "H": H, "tp": tp, "sl": sl, "exit": ex, "trades": n,
                         "avg_net": float(nets.mean()) if n else np.nan, "win": float((nets > 0).mean()) if n else np.nan,
                         "tstat": float(nets.mean() / (nets.std(ddof=1) + 1e-12) * np.sqrt(n)) if n > 2 else np.nan})
    df = pd.DataFrame(rows)
    w = df.pivot_table(index=["H", "tp", "sl", "exit"], columns="period", values=["avg_net", "tstat", "trades"])
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w["robust"] = w[["tstat_early", "tstat_mid"]].min(axis=1)
    w = w.sort_values("robust", ascending=False).reset_index()
    w.to_csv(ROOT / "reports" / "short_mirror_5m.csv", index=False)
    cols = ["H", "tp", "sl", "exit"] + [f"{m}_{p}" for p in ("early", "mid", "locked", "hold25") for m in ("avg_net", "tstat", "trades")]
    print(w[cols].head(8).to_string(index=False))


if __name__ == "__main__":
    main()
