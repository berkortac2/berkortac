"""Locked final test.

1. Rank method families by their walk-forward validation score (search period).
2. For the top families take, per timeframe, the best configuration, refit it
   once on the whole search period (frozen, exactly what goes into Pine) and
   evaluate it ONCE on the locked final-test period, on the training coins and
   on coins the model has never seen.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .dataset import ROOT, Dataset, final_cutoff, load_cfg, load_dataset, load_universe, tf_ms
from .eval.metrics import deflated_sharpe_prob, trade_metrics
from .labels import simulate, trade_outcomes
from .models import offline as off
from .models.online import lorentzian_knn, online_logit
from .search.runner import (BARS_PER_YEAR, DISCRETE, KNN_SETS, SEARCH_COINS, model_feature_sets)

FAMILIES = ["logit", "gbm", "knn", "online_logit", "rules", "meta"]
FAMILY_TR = {
    "logit": "Lojistik skor modeli (korelasyon katmanlı)",
    "gbm": "Sığ Gradient Boosting (LightGBM)",
    "knn": "Lorentzian kNN (mum-mum kendi kendine öğrenen)",
    "online_logit": "Online lojistik (her mumda ağırlık güncelleyen)",
    "rules": "Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon)",
    "meta": "Meta-labeling (kural + model onayı)",
    "baseline": "Baz çizgisi",
}
TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
ONLINE_WARM = 5000


def load_trials(search_dir: Path) -> pd.DataFrame:
    parts = [pd.read_parquet(p) for p in sorted(Path(search_dir).glob("trials_*.parquet"))]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def rank_families(trials: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fam in FAMILIES:
        per_tf = []
        for tf in TFS:
            t = trials[(trials.tf == tf) & (trials.family == fam)]
            if t.empty:
                continue
            best = t.sort_values("score", ascending=False).iloc[0]
            per_tf.append({"tf": tf, "score": max(best.score, -5.0), "avg_net": best.get("avg_net", np.nan),
                           "dir_hit": best.get("dir_hit", np.nan)})
        if not per_tf:
            continue
        d = pd.DataFrame(per_tf)
        rows.append({"family": fam, "n_tf": len(d), "mean_score": d.score.mean(),
                     "tf_positive": int((d.avg_net > 0).sum()), "mean_dir_hit": d.dir_hit.mean()})
    return pd.DataFrame(rows).sort_values("mean_score", ascending=False).reset_index(drop=True)


def best_trial(trials, tf, family):
    t = trials[(trials.tf == tf) & (trials.family == family)]
    if t.empty:
        return None
    return t.sort_values("score", ascending=False).iloc[0]


def parse_config(cfg: str) -> dict:
    parts = cfg.split(":")
    out = {"kind": parts[0], "name": parts[1] if len(parts) > 1 else ""}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                out[k] = float(v) if "." in v or "e" in v else int(v)
            except ValueError:
                out[k] = v
    return out


class FinalModel:
    """A frozen, fully specified signal model for one timeframe."""

    def __init__(self, tf, trial: pd.Series, search_ds: Dataset, cost: float):
        self.tf = tf
        self.trial = trial
        self.family = trial.family
        self.config = trial.config
        self.H = int(trial.H)
        self.tp = None if pd.isna(trial.tp_atr) else float(trial.tp_atr)
        self.sl = None if pd.isna(trial.sl_atr) else float(trial.sl_atr)
        self.q = None if pd.isna(trial.get("q", np.nan)) else float(trial.q)
        self.cost = cost
        self.names = search_ds.feat_names
        self.fidx = {n: i for i, n in enumerate(self.names)}
        self.export = {"tf": tf, "family": self.family, "config": self.config, "H": self.H,
                       "tp_atr": self.tp, "sl_atr": self.sl, "q": self.q}
        self._fit(search_ds)

    # -------------------------------------------------------------- fitting
    def _train_rows(self, ds):
        lim = final_cutoff(self.tf) - (self.H + 1) * tf_ms(self.tf)
        return np.flatnonzero(ds.time < lim)

    def _fit(self, ds: Dataset):
        c = parse_config(self.config)
        kind = c["kind"]
        tr = self._train_rows(ds)
        y = off.binary_target(ds.fwd_ret_atr(self.H))
        sets = model_feature_sets(self.names)
        self.kind = kind
        if kind in ("logit", "gbm"):
            name = c["name"]
            feats = sets["all"] if name == "all_top20" else sets[name]
            fi = [self.fidx[f] for f in feats]
            if kind == "logit":
                self.model = off.Logit(fi, C=0.05, top_k=20 if name == "all_top20" else None).fit(ds.X, y, tr)
                m = self.model
                self.export.update(features=[self.names[i] for i in m.feat_idx], w=m.w.tolist(), b=m.b,
                                   mu=m.std.mu.tolist(), sd=m.std.sd.tolist())
            else:
                self.model = off.GBM(fi, n_estimators=int(c.get("n_estimators", 80))).fit(ds.X, y, tr)
                self.export.update(features=[self.names[i] for i in self.model.feat_idx],
                                   trees=self.model.model.booster_.dump_model()["tree_info"])
            self.train_scores = self.model.score(ds.X, tr[:: max(1, len(tr) // 200_000)])
        elif kind == "knn":
            self.knn_feats = KNN_SETS[c["name"]]
            self.k, self.window, self.step = int(c["k"]), int(c["w"]), int(c["s"])
            sc = self._knn_scores(ds)
            self.train_scores = sc[tr]
            self.export.update(features=self.knn_feats, k=self.k, window=self.window, step=self.step)
        elif kind == "online_logit":
            self.prior = off.Logit([self.fidx[f] for f in sets["all"]], C=0.05, top_k=20).fit(ds.X, y, tr)
            self.lr, self.l2 = float(c["lr"]), float(c["l2"])
            self.zero = c.get("prior") == "zero"
            sc = self._online_scores(ds)
            self.train_scores = sc[tr]
            p = self.prior
            self.export.update(features=[self.names[i] for i in p.feat_idx], w=p.w.tolist(), b=p.b,
                               mu=p.std.mu.tolist(), sd=p.std.sd.tolist(), lr=self.lr, l2=self.l2,
                               zero_prior=self.zero)
        elif kind in ("rules", "meta"):
            g = ds.fwd_net(self.H, self.cost)
            prims = off.make_primitives(ds.X, self.names, tr, DISCRETE)
            self.rules = {}
            for d, tgt in ((1, g - self.cost), (-1, -g - self.cost)):
                self.rules[d] = off.beam_search(ds.X, tgt, tr, prims, self.H)
            top = 3 if self.config.endswith("top3_or") else 1
            self.rules_used = {d: [r for r, _ in self.rules[d][:top]] for d in (1, -1)}
            self.export.update(rules={str(d): [[(self.names[j], op, v) for j, op, v in r] for r in rr]
                                      for d, rr in self.rules_used.items()})
            if kind == "meta":
                self.meta_model = off.Logit([self.fidx[f] for f in sets["all"]], C=0.05).fit(ds.X, y, tr)
                m = self.meta_model
                self.export.update(meta_features=[self.names[i] for i in m.feat_idx], meta_w=m.w.tolist(),
                                   meta_b=m.b, meta_mu=m.std.mu.tolist(), meta_sd=m.std.sd.tolist())
        elif kind == "baseline":
            pass
        if self.q is not None and hasattr(self, "train_scores"):
            self.thr_hi = max(float(np.quantile(self.train_scores, 1 - self.q)), 1e-9)
            self.thr_lo = min(float(np.quantile(self.train_scores, self.q)), -1e-9)
            self.export.update(thr_long=self.thr_hi, thr_short=self.thr_lo)

    # -------------------------------------------------------------- scoring
    def _knn_scores(self, ds):
        fi = [self.fidx[f] for f in self.knn_feats]
        fwd = ds.fwd_ret_atr(self.H)
        lab = np.where(fwd > off.DEADZONE, 1.0, np.where(fwd < -off.DEADZONE, -1.0, 0.0))
        lab[np.isnan(fwd)] = np.nan
        return lorentzian_knn(np.ascontiguousarray(ds.X[:, fi]), lab, ds.seg_start, ds.seg_end, self.H,
                              self.k, self.window, self.step)

    def _online_scores(self, ds):
        p = self.prior
        Xs = np.ascontiguousarray(p.std(ds.X[:, p.feat_idx].astype(np.float64)))
        y = off.binary_target(ds.fwd_ret_atr(self.H))
        w0 = np.zeros(len(p.feat_idx)) if self.zero else p.w.astype(np.float64)
        b0 = 0.0 if self.zero else p.b
        return online_logit(Xs, y, ds.seg_start, ds.seg_end, self.H, w0, b0, self.lr, self.l2)

    def signals(self, ds: Dataset):
        k = self.kind
        n = ds.n
        if k in ("logit", "gbm", "knn", "online_logit"):
            if k in ("logit", "gbm"):
                sc = self.model.score(ds.X)
            elif k == "knn":
                sc = self._knn_scores(ds)
            else:
                sc = self._online_scores(ds)
            return sc >= self.thr_hi, sc <= self.thr_lo, sc
        if k in ("rules", "meta"):
            L = np.zeros(n, bool); S = np.zeros(n, bool)
            for r in self.rules_used[1]:
                L |= off.rule_mask(ds.X, r)
            for r in self.rules_used[-1]:
                S |= off.rule_mask(ds.X, r)
            if k == "meta":
                sc = self.meta_model.score(ds.X)
                L &= sc > 0
                S &= sc < 0
            return L, S, None
        if k == "baseline":
            return baseline_signals(ds, self.config) + (None,)
        raise ValueError(k)


def baseline_signals(ds: Dataset, config: str):
    if config.endswith("rsi_30_70_cross"):
        rsi = ds.col("rsi").astype(np.float64)
        prev = np.concatenate([[np.nan], rsi[:-1]])
        prev[ds.seg_start] = np.nan
        return (prev < -0.4) & (rsi >= -0.4), (prev > 0.4) & (rsi <= 0.4)
    if config.endswith("macd_cross"):
        mc = ds.col("macd_cross")
        return mc > 0, mc < 0
    r = np.random.default_rng(42).random(ds.n)
    return r < 0.02, r > 0.98


def evaluate_frozen(model: FinalModel, ds: Dataset, t_from: int, cost: float, tf: str):
    L, S, sc = model.signals(ds)
    live = ds.time >= t_from
    L &= live
    S &= live
    aa = 1e6 if model.tp is None else model.tp
    bb = 1e6 if model.sl is None else model.sl
    outs = {}
    for d in (1, -1):
        net = np.full(ds.n, np.nan); ex = np.full(ds.n, -1, np.int64); hit = np.full(ds.n, np.nan)
        for s, e in zip(ds.seg_start, ds.seg_end):
            n_, x_, h_ = trade_outcomes(ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e], ds.atr[s:e],
                                        model.H, aa, bb, cost, d)
            net[s:e], hit[s:e] = n_, h_
            ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
        outs[d] = (net, ex, hit)
    rows, dirs, nets, hits = simulate(L, S, *outs[1], *outs[-1], ds.seg_start, ds.seg_end)
    n_bars = int(live.sum())
    m = trade_metrics(dirs, nets, hits, n_bars, BARS_PER_YEAR[tf], len(ds.symbols))
    trades = pd.DataFrame({"time": ds.time[rows], "coin": [ds.symbols[i] for i in ds.coin[rows]],
                           "dir": dirs, "net": nets, "hit": hits})
    return m, trades


def run_final(search_dir: Path, out_dir: Path, top_n: int = 3, tfs=None, log=print):
    cfg = load_cfg()
    uni = load_universe()
    cost = 2 * (cfg["fee_per_side"] + cfg["slippage_per_side"])
    trials = load_trials(search_dir)
    ranking = rank_families(trials)
    ranking.to_csv(out_dir / "family_ranking_validation.csv", index=False)
    top = ranking.family.head(top_n).tolist()
    log(f"family ranking:\n{ranking.to_string()}\nTOP: {top}")
    results, exports, all_trades = [], {}, []
    for tf in tfs or TFS:
        if trials[trials.tf == tf].empty:
            continue
        cut = final_cutoff(tf)
        search_ds = load_dataset(tf, uni["train"][:SEARCH_COINS[tf]], t_max=cut)
        warm_ms = ONLINE_WARM * tf_ms(tf)
        test_sets = {
            "train_coins": load_dataset(tf, uni["train"], t_min=cut - warm_ms),
            "unseen_coins": load_dataset(tf, uni["unseen"], t_min=cut - warm_ms),
        }
        tt = trials[trials.tf == tf]
        n_trials = len(tt)
        valid = tt[tt.trades > 1]
        srs = (valid.tstat / np.sqrt(valid.trades)).replace([np.inf, -np.inf], np.nan).dropna()
        sr_var = float(srs.var()) if len(srs) > 1 else 0.0
        fams = top + ["baseline"]
        for fam in fams:
            if fam == "baseline":
                ref = best_trial(trials, tf, top[0])
                cand = []
                for bc in ("baseline:rsi_30_70_cross", "baseline:macd_cross", "baseline:random_2pct"):
                    r = ref.copy()
                    r["family"], r["config"], r["q"] = "baseline", bc, np.nan
                    cand.append(r)
            else:
                bt = best_trial(trials, tf, fam)
                cand = [bt] if bt is not None else []
            for trial in cand:
                log(f"[{tf}] final fit {fam} {trial.config} H={trial.H} tp={trial.tp_atr} sl={trial.sl_atr} q={trial.get('q')}")
                model = FinalModel(tf, trial, search_ds, cost)
                if fam in top:
                    exports.setdefault(fam, {})[tf] = model.export
                for group, ds in test_sets.items():
                    if ds.n == 0:
                        continue
                    m, trades = evaluate_frozen(model, ds, cut, cost, tf)
                    sr = m.get("tstat", 0) / math.sqrt(m["trades"]) if m.get("trades", 0) > 1 else 0.0
                    m["dsr_prob"] = deflated_sharpe_prob(sr, m.get("trades", 0), n_trials, sr_var) if m.get("trades", 0) > 2 else float("nan")
                    row = {"tf": tf, "family": fam, "config": trial.config, "group": group, "H": int(trial.H),
                           "tp_atr": trial.tp_atr, "sl_atr": trial.sl_atr, "q": trial.get("q"),
                           "val_score": trial.score, "val_dir_hit": trial.get("dir_hit"),
                           "val_win_rate": trial.get("win_rate"), "val_avg_net": trial.get("avg_net"),
                           "n_trials_tf": n_trials, **m}
                    results.append(row)
                    if fam in top:
                        trades["tf"], trades["family"], trades["group"] = tf, fam, group
                        all_trades.append(trades)
                    log(f"   {group}: trades={m.get('trades')} dir_hit={m.get('dir_hit', float('nan')):.3f} "
                        f"win={m.get('win_rate', float('nan')):.3f} avg_net={m.get('avg_net', float('nan')):.5f}")
        pd.DataFrame(results).to_csv(out_dir / "final_results.csv", index=False)
        (out_dir / "final_models.json").write_text(json.dumps(exports, indent=1, default=float))
    if all_trades:
        pd.concat(all_trades).to_parquet(out_dir / "final_trades.parquet", index=False)
    return pd.DataFrame(results), ranking
