"""Pre-registered selection + locked final test of the second-round 5m search, then export
to the bot (bot/model/model_5m.json), the main Pine indicator and the scanner.

Selection (fixed BEFORE the locked period is loaded; only validation columns are used):
  * PRECISE long  = best-score rules trial trading long only
  * PRECISE short = best-score rules trial trading short only, kept only if score >= 1.5,
                    pos_folds >= 0.75 and avg_net > 0
  * FREQUENT      = among long rules trials with score >= 5, positive in all 4 folds and at least
                    1.5x the precise trade frequency: the largest validation net profit per coin-day
                    (fixed after seeing VALIDATION results only; the locked period is still untouched)
  * BOT/PINE model = the variant (precise / frequent) with the larger validation
                    net profit per coin-day among those with t-stat >= 2
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.dataset import final_cutoff, load_dataset, load_universe, tf_ms  # noqa: E402
from tsa.eval.metrics import deflated_sharpe_prob, trade_metrics  # noqa: E402
from tsa.labels import simulate, trade_outcomes  # noqa: E402
from tsa.models import offline as off  # noqa: E402
from tsa.search.deep5m import FEATURE_DIR, RULE_QS, RULE_ROWS, BEAM, SPOT_SEGMENTS  # noqa: E402
from tsa.search.runner import DISCRETE  # noqa: E402

REP = ROOT / "reports"
COST = 0.0014
FEES = {"futures_taker": 0.0014, "futures_maker_entry": 0.0009, "futures_maker": 0.0004, "spot_taker_long": 0.0024}
V1 = json.loads((REP / "final_models.json").read_text())["rules"]["5m"]


def parse(cfg: str) -> dict:
    # rules:d3s0.002:long:top3
    _, tag, side, top = cfg.split(":")
    d = int(tag[1]); ms = float(tag.split("s", 1)[1])
    return {"depth": d, "min_support": ms, "side": side, "top": int(top[3:])}


def pick(tr: pd.DataFrame) -> dict:
    r = tr[(tr.family == "rules") & (tr.score > -99)].copy()
    r["side"] = r.config.str.split(":").str[2]
    out = {}
    L = r[r.side == "long"].sort_values("score", ascending=False)
    S = r[r.side == "short"].sort_values("score", ascending=False)
    out["precise_long"] = L.iloc[0]
    base_f = out["precise_long"].trades_per_coin_day
    Lf = L[(L.trades_per_coin_day >= 1.5 * base_f) & (L.score >= 5) & (L.pos_folds >= 1.0)]
    Lf = Lf.sort_values("net_per_coin_day", ascending=False)
    out["frequent_long"] = Lf.iloc[0] if len(Lf) else None
    ok = S[(S.score >= 1.5) & (S.pos_folds >= 0.75) & (S.avg_net > 0)]
    out["precise_short"] = ok.iloc[0] if len(ok) else None
    okf = ok[ok.trades_per_coin_day >= 1.5 * base_f].sort_values("net_per_coin_day", ascending=False)
    out["frequent_short"] = okf.iloc[0] if len(okf) else None
    return out


def fit_rules(ds, trial, cutoff):
    p = parse(trial.config)
    H = int(trial.H)
    tr = np.flatnonzero(ds.time < cutoff - (H + 1) * tf_ms("5m"))
    g = ds.fwd_net(H, COST)
    tgt = (g - COST) if p["side"] == "long" else (-g - COST)
    prims = off.make_primitives(ds.X, ds.feat_names, tr, DISCRETE, qs=RULE_QS)
    rules = off.beam_search(ds.X, tgt, tr, prims, H, depth=p["depth"], beam=BEAM, min_support=p["min_support"],
                            max_rows=RULE_ROWS)[:p["top"]]
    return [[(ds.feat_names[j], op, float(v)) for j, op, v in r] for r, _ in rules]


def dir_spec(trial, rules):
    if trial is None:
        return {"rules": [], "H": 24, "tp_atr": None, "sl_atr": None}
    f = lambda x: None if (x is None or (isinstance(x, float) and math.isnan(x))) else float(x)  # noqa: E731
    return {"rules": [[list(c) for c in r] for r in rules], "H": int(trial.H), "tp_atr": f(trial.tp_atr),
            "sl_atr": f(trial.sl_atr), "val": {k: float(trial[k]) for k in
                                                ("score", "trades", "dir_hit", "win_rate", "avg_net", "pos_folds",
                                                 "trades_per_coin_day", "net_per_coin_day")}, "config": trial.config}


def mask(ds, rules):
    fi = {n: i for i, n in enumerate(ds.feat_names)}
    m = np.zeros(ds.n, bool)
    for r in rules:
        m |= off.rule_mask(ds.X, [(fi[f], op, v) for f, op, v in r])
    return m


def evaluate(ds, model, t_from, t_to=None):
    live = ds.time >= t_from
    if t_to is not None:
        live &= ds.time < t_to
    L = mask(ds, model["long"]["rules"]) & live
    S = mask(ds, model["short"]["rules"]) & live
    res = {}
    outs = {}
    for d, spec in ((1, model["long"]), (-1, model["short"])):
        a = 1e6 if spec["tp_atr"] is None else spec["tp_atr"]
        b = 1e6 if spec["sl_atr"] is None else spec["sl_atr"]
        net = np.full(ds.n, np.nan); ex = np.full(ds.n, -1, np.int64); hit = np.full(ds.n, np.nan)
        for s, e in zip(ds.seg_start, ds.seg_end):
            n_, x_, h_ = trade_outcomes(ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e], ds.atr[s:e], spec["H"], a, b, COST, d)
            net[s:e], hit[s:e] = n_, h_
            ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
        outs[d] = (net, ex, hit)
    rows, dirs, nets, hits = simulate(L, S, *outs[1], *outs[-1], ds.seg_start, ds.seg_end)
    n_bars = int(live.sum())
    days = n_bars / 288
    for fee, c in FEES.items():
        if fee == "spot_taker_long":
            sel = dirs > 0
            nn = nets[sel] + COST - c
            m = trade_metrics(dirs[sel], nn, hits[sel], n_bars, 105120, len(ds.symbols))
        else:
            nn = nets + COST - c
            m = trade_metrics(dirs, nn, hits, n_bars, 105120, len(ds.symbols))
        m["trades_per_coin_day"] = m["trades"] / max(days, 1e-9)
        m["net_per_coin_day"] = float(nn.sum()) / max(days, 1e-9)
        res[fee] = m
    trades = pd.DataFrame({"time": ds.time[rows], "coin": [ds.symbols[i] for i in ds.coin[rows]], "dir": dirs,
                           "net": nets, "hit": hits})
    return res, trades


def main():
    tr = pd.read_parquet(REP / "search" / "trials_5m_deep.parquet")
    sel = pick(tr)
    cut = final_cutoff("5m")
    uni = load_universe()
    print({k: (None if v is None else (v.config, int(v.H), v.tp_atr, v.sl_atr, round(v.score, 2),
                                       round(v.trades_per_coin_day, 3))) for k, v in sel.items()}, flush=True)
    ds = load_dataset("5m", uni["search5m"] + SPOT_SEGMENTS, t_max=cut, feature_dir=FEATURE_DIR)
    fitted = {k: (fit_rules(ds, v, cut) if v is not None else []) for k, v in sel.items()}
    del ds
    models = {
        "precise": {"long": dir_spec(sel["precise_long"], fitted["precise_long"]),
                    "short": dir_spec(sel["precise_short"], fitted["precise_short"])},
        "frequent": {"long": dir_spec(sel["frequent_long"], fitted["frequent_long"]),
                     "short": dir_spec(sel["frequent_short"] if sel["frequent_short"] is not None else sel["precise_short"],
                                       fitted["frequent_short"] if sel["frequent_short"] is not None else fitted["precise_short"])},
        "v1": {"long": {"rules": V1["rules"]["1"], "H": V1["H"], "tp_atr": V1["tp_atr"], "sl_atr": V1["sl_atr"]},
               "short": {"rules": [], "H": V1["H"], "tp_atr": None, "sl_atr": None}},
    }
    if not models["frequent"]["long"]["rules"]:
        models.pop("frequent")

    # pre-registered bot choice (validation only)
    def val_npcd(m):
        return sum(m[d].get("val", {}).get("net_per_coin_day", 0.0) for d in ("long", "short"))
    cands = [k for k in ("precise", "frequent") if k in models
             and models[k]["long"].get("val", {}).get("score", 0) >= 2.0]
    chosen = max(cands, key=lambda k: val_npcd(models[k])) if cands else "precise"
    (REP / "deep5m_models.json").write_text(json.dumps({"chosen": chosen, "models": models}, indent=1))
    print("chosen (pre-registered):", chosen, flush=True)

    # locked test ---------------------------------------------------------------
    groups = {
        "search12": (uni["search5m"], cut, None),
        "other_train8": ([s for s in uni["train"] if s not in uni["search5m"]], cut, None),
        "unseen6": (uni["unseen"], cut, None),
        "holdout10": (uni["holdout2"], cut, None),
        "holdout10_pre": (uni["holdout2"], 0, cut),
    }
    n_trials = len(tr) + len(pd.read_parquet(REP / "search" / "trials_5m.parquet"))
    valid = tr[tr.trades > 1]
    sr_var = float((valid.tstat / np.sqrt(valid.trades)).replace([np.inf, -np.inf], np.nan).dropna().var())
    rows, all_trades = [], []
    for g, (syms, t0, t1) in groups.items():
        ds = load_dataset("5m", syms, t_min=(t0 - 400 * 300_000) if t0 else None, t_max=t1, feature_dir=FEATURE_DIR)
        for name, m in models.items():
            res, trades = evaluate(ds, m, t0, t1)
            for fee, r in res.items():
                n = r.get("trades", 0)
                sr = r.get("tstat", 0) / math.sqrt(n) if n > 1 else 0.0
                r["dsr_prob"] = deflated_sharpe_prob(sr, n, n_trials, sr_var) if n > 2 else float("nan")
                rows.append({"group": g, "model": name, "fee": fee, "coins": len(ds.symbols), **r})
            trades["group"], trades["model"] = g, name
            all_trades.append(trades)
            r = res["futures_taker"]
            print(f"{g:14s} {name:9s} trades={r.get('trades')} tpcd={r['trades_per_coin_day']:.3f} "
                  f"dir={r.get('dir_hit', float('nan')):.3f} win={r.get('win_rate', float('nan')):.3f} "
                  f"avg={r.get('avg_net', float('nan')):.5f} L={r.get('long_trades')} S={r.get('short_trades')} "
                  f"S_avg={r.get('short_avg_net', float('nan')):.5f}", flush=True)
        del ds
    pd.DataFrame(rows).to_csv(REP / "deep5m_final_results.csv", index=False)
    pd.concat(all_trades).to_parquet(REP / "deep5m_final_trades.parquet", index=False)


if __name__ == "__main__":
    main()
