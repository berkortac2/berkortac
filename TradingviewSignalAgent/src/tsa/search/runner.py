"""Strategy search for one timeframe (purged, embargoed walk-forward replay).

Timeline of the *search period* (the locked final-test period is never loaded):

   |---- train (expanding) ----|embargo| test fold k |
   the last 60% of the search period is cut into 4 test folds; each fold's
   models are trained only on rows whose labels matured before the fold began.

Online learners (kNN, online logit) replay each coin bar by bar over the
whole period and are scored on the same test folds.

Every (method, config, horizon, barrier, threshold) combination is a *trial*;
all trials are logged so the selection can be deflated for multiple testing.
"""
from __future__ import annotations

import itertools
import json
import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

from ..dataset import final_cutoff, load_cfg, load_dataset, load_universe, tf_ms
from ..eval.metrics import trade_metrics
from ..labels import simulate, trade_outcomes
from ..models import offline as off
from ..models.online import lorentzian_knn, online_logit

BARRIERS = [(1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (1.5, 1.5), (2.0, 2.0), (3.0, 1.5), (None, None)]
QS = [0.005, 0.01, 0.02, 0.05, 0.10]
BARS_PER_YEAR = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520, "1h": 8760, "4h": 2190, "1d": 365, "1w": 52}
SEARCH_COINS = {"1m": 6, "5m": 10, "15m": 10, "30m": 12, "1h": 20, "4h": 20, "1d": 20, "1w": 20}
MIN_TRAIN = {"1w": 300, "1d": 2000}
MIN_TRADES = {"1m": 200, "5m": 200, "15m": 150, "30m": 120, "1h": 100, "4h": 60, "1d": 30, "1w": 12}

GROUPS = {
    "mom": ["rsi", "rsi7", "rsi21", "rsi_slope3", "rsi_z", "macd_atr", "sig_atr", "hist_atr", "hist_slope",
            "hist_slope3", "macd_cross", "mfi", "adx", "di", "ema50_dist", "ema200_dist", "ema50_slope",
            "atr_pct", "atr_ratio", "cci", "wt", "wt_diff", "stoch"],
    "cdl": ["body", "upwick", "lowwick", "clv", "range_atr", "ret1_atr", "ret3_atr", "ret5_atr", "ret10_atr",
            "streak", "engulf", "pin", "chan20", "chan50", "chan100"],
    "vol": ["relvol", "vol_z", "svflow10", "svflow30", "cmf20", "vol_trend"],
    "div": ["div_rsi_bull", "div_rsi_hbull", "div_rsi_bear", "div_rsi_hbear", "div_hist_bull", "div_hist_hbull",
            "div_hist_bear", "div_hist_hbear", "div_rsi_bull5", "div_rsi_bear5", "divc14"],
    "time": ["hour_sin", "hour_cos", "dow_sin", "dow_cos"],
    "htf": ["htf_rsi", "htf_hist_atr", "htf_ema50_dist", "htf_chan20"],
}
DISCRETE = {"engulf", "pin", "macd_cross", "div_rsi_bull", "div_rsi_hbull", "div_rsi_bear", "div_rsi_hbear",
            "div_hist_bull", "div_hist_hbull", "div_hist_bear", "div_hist_hbear", "div_rsi_bull5", "div_rsi_bear5"}

KNN_SETS = {
    "ldc_classic": ["rsi", "wt", "cci", "adx", "rsi7"],
    "mom_vol": ["rsi", "hist_atr", "relvol", "svflow10", "cmf20"],
    "corr": ["c_close_rsi20", "c_close_obv20", "c_body_vol20", "m_rsi_mfi20", "cc_closeobv_closersi50"],
    "mixed": ["rsi", "wt", "hist_atr", "c_close_obv20", "c_body_vol20", "svflow10", "adx"],
}


def feature_groups(names: list[str]) -> dict[str, list[str]]:
    g = {k: [n for n in v if n in names] for k, v in GROUPS.items()}
    g["corr_cm"] = [n for n in names if n.startswith("c_")]
    g["corr_mm"] = [n for n in names if n.startswith("m_")]
    g["corr2"] = [n for n in names if n.startswith(("cc_", "d5_", "z_"))]
    return g


def model_feature_sets(names):
    g = feature_groups(names)
    base = g["mom"]
    sets = {
        "mom": base,
        "mom_cdl_vol": base + g["cdl"] + g["vol"],
        "mom_cdl_vol_div": base + g["cdl"] + g["vol"] + g["div"],
        "plus_corr": base + g["cdl"] + g["vol"] + g["div"] + g["corr_cm"] + g["corr_mm"],
        "plus_corr2": base + g["cdl"] + g["vol"] + g["div"] + g["corr_cm"] + g["corr_mm"] + g["corr2"],
        "all": list(names),
    }
    return sets


def make_folds(t: np.ndarray, n_folds: int, test_frac: float = 0.6):
    t0, t1 = int(t.min()), int(t.max()) + 1
    start = t0 + (1 - test_frac) * (t1 - t0)
    edges = np.linspace(start, t1, n_folds + 1).astype(np.int64)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n_folds)]


class Search:
    def __init__(self, tf: str, out_dir: Path, n_coins: int | None = None, horizons=None, log=print):
        self.cfg = load_cfg()
        self.tf = tf
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = log
        uni = load_universe()
        n_coins = n_coins or SEARCH_COINS[tf]
        self.cutoff = final_cutoff(tf)
        self.ds = load_dataset(tf, uni["train"][:n_coins], t_max=self.cutoff)
        self.horizons = horizons or self.cfg["timeframes"][tf]["horizons"]
        self.cost = 2 * (self.cfg["fee_per_side"] + self.cfg["slippage_per_side"])
        self.folds = make_folds(self.ds.time, self.cfg["walkforward"]["n_folds"])
        self.embargo = self.cfg["walkforward"]["embargo_bars"]
        self.names = self.ds.feat_names
        self.fidx = {n: i for i, n in enumerate(self.names)}
        self.trials: list[dict] = []
        self.test_mask = np.zeros(self.ds.n, bool)
        self.fold_id = np.full(self.ds.n, -1, np.int8)
        for k, (a, b) in enumerate(self.folds):
            m = (self.ds.time >= a) & (self.ds.time < b)
            self.test_mask |= m
            self.fold_id[m] = k
        self.n_test_bars = int(self.test_mask.sum())
        self._outcomes = {}
        self.artifacts = {}
        log(f"[{tf}] rows={self.ds.n:,} coins={len(self.ds.symbols)} test_rows={self.n_test_bars:,} "
            f"feats={len(self.names)} cutoff={pd.Timestamp(self.cutoff, unit='ms')}")

    # ------------------------------------------------------------------ helpers
    def train_idx(self, k: int, H: int) -> np.ndarray:
        start = self.folds[k][0]
        lim = start - (H + 1 + self.embargo) * tf_ms(self.tf)
        return np.flatnonzero(self.ds.time < lim)

    def test_idx(self, k: int) -> np.ndarray:
        return np.flatnonzero(self.fold_id == k)

    def outcomes(self, H, a, b, direction):
        key = (H, a, b, direction)
        if key not in self._outcomes:
            ds = self.ds
            aa = 1e6 if a is None else a
            bb = 1e6 if b is None else b
            net = np.full(ds.n, np.nan)
            ex = np.full(ds.n, -1, np.int64)
            hit = np.full(ds.n, np.nan)
            for s, e in zip(ds.seg_start, ds.seg_end):
                n_, x_, h_ = trade_outcomes(ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e], ds.atr[s:e],
                                            H, aa, bb, self.cost, direction)
                net[s:e], hit[s:e] = n_, h_
                ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
            self._outcomes[key] = (net, ex, hit)
        return self._outcomes[key]

    def evaluate(self, long_sig, short_sig, H, family, config, extra=None):
        """Evaluate a signal pair on every barrier config; log one trial per config."""
        ds = self.ds
        long_sig = long_sig & self.test_mask
        short_sig = short_sig & self.test_mask
        if long_sig.sum() + short_sig.sum() == 0:
            return
        for a, b in BARRIERS:
            nl, el, hl = self.outcomes(H, a, b, 1)
            ns, es, hs = self.outcomes(H, a, b, -1)
            rows, dirs, nets, hits = simulate(long_sig, short_sig, nl, el, hl, ns, es, hs, ds.seg_start, ds.seg_end)
            m = trade_metrics(dirs, nets, hits, self.n_test_bars, BARS_PER_YEAR[self.tf], len(ds.symbols))
            fold_means = []
            for k in range(len(self.folds)):
                fm = self.fold_id[rows] == k
                fold_means.append(float(nets[fm].mean()) if fm.sum() >= 5 else float("nan"))
            fm_arr = np.array(fold_means)
            pos = np.nansum(fm_arr > 0) / len(self.folds)
            ok = m["trades"] >= MIN_TRADES[self.tf]
            score = (m.get("tstat", 0.0) * pos) if ok else -99.0
            row = {"tf": self.tf, "family": family, "config": config, "H": H,
                   "tp_atr": a, "sl_atr": b, "score": score, "pos_folds": pos,
                   **{f"fold{k}_avg_net": v for k, v in enumerate(fold_means)}, **m}
            if extra:
                row.update(extra)
            self.trials.append(row)

    def thresholds_eval(self, scores_by_fold: dict, H, family, config):
        """scores_by_fold: k -> (train_scores_sample, test_idx, test_scores)."""
        for q in QS:
            L = np.zeros(self.ds.n, bool)
            S = np.zeros(self.ds.n, bool)
            thr = {}
            for k, (trs, tidx, tsc) in scores_by_fold.items():
                if trs is None or len(trs) < 100:
                    continue
                hi = max(np.quantile(trs, 1 - q), 1e-9)
                lo = min(np.quantile(trs, q), -1e-9)
                L[tidx] = tsc >= hi
                S[tidx] = tsc <= lo
                thr[k] = (float(hi), float(lo))
            self.evaluate(L, S, H, family, config, {"q": q, "thr_last": json.dumps(thr.get(len(self.folds) - 1))})

    # ------------------------------------------------------------------ families
    def run_offline(self, H, kind, set_name, feats, **kw):
        ds = self.ds
        y = off.binary_target(ds.fwd_ret_atr(H))
        fi = [self.fidx[f] for f in feats if f in self.fidx]
        by_fold = {}
        last_model = None
        for k in range(len(self.folds)):
            tr = self.train_idx(k, H)
            if len(tr) < MIN_TRAIN.get(self.tf, 5000):
                continue
            model = off.Logit(fi, **kw) if kind == "logit" else off.GBM(fi, **kw)
            model.fit(ds.X, y, tr)
            smp = tr[np.random.default_rng(k).choice(len(tr), min(len(tr), 100_000), replace=False)]
            te = self.test_idx(k)
            by_fold[k] = (model.score(ds.X, smp), te, model.score(ds.X, te))
            last_model = model
        cfg = f"{kind}:{set_name}" + "".join(f":{a}={b}" for a, b in kw.items())
        self.thresholds_eval(by_fold, H, kind, cfg)
        return last_model, by_fold

    def run_knn(self, H, set_name, feats, k=8, window=2000, step=4):
        ds = self.ds
        fi = [self.fidx[f] for f in feats if f in self.fidx]
        fwd = ds.fwd_ret_atr(H)
        lab = np.where(fwd > off.DEADZONE, 1.0, np.where(fwd < -off.DEADZONE, -1.0, 0.0))
        lab[np.isnan(fwd)] = np.nan
        X = np.ascontiguousarray(ds.X[:, fi])
        sc = lorentzian_knn(X, lab, ds.seg_start, ds.seg_end, H, k, window, step)
        by_fold = {}
        for kf in range(len(self.folds)):
            tr = self.train_idx(kf, H)
            if len(tr) < MIN_TRAIN.get(self.tf, 5000):
                continue
            smp = tr[np.random.default_rng(kf).choice(len(tr), min(len(tr), 100_000), replace=False)]
            te = self.test_idx(kf)
            by_fold[kf] = (sc[smp], te, sc[te])
        self.thresholds_eval(by_fold, H, "knn", f"knn:{set_name}:k={k}:w={window}:s={step}")
        return sc

    def run_online_logit(self, H, prior_models: dict, lr, l2, tag):
        ds = self.ds
        y = off.binary_target(ds.fwd_ret_atr(H))
        by_fold = {}
        for kf, model in prior_models.items():
            fi = model.feat_idx
            Xs = np.ascontiguousarray(model.std(ds.X[:, fi].astype(np.float64)))
            w0 = model.w.astype(np.float64) if tag != "zero" else np.zeros(len(fi))
            b0 = model.b if tag != "zero" else 0.0
            sc = online_logit(Xs, y, ds.seg_start, ds.seg_end, H, w0, b0, lr, l2)
            tr = self.train_idx(kf, H)
            smp = tr[np.random.default_rng(kf).choice(len(tr), min(len(tr), 100_000), replace=False)]
            te = self.test_idx(kf)
            by_fold[kf] = (sc[smp], te, sc[te])
        self.thresholds_eval(by_fold, H, "online_logit", f"online_logit:prior={tag}:lr={lr}:l2={l2}")

    def run_rules(self, H, logit_by_fold=None):
        ds = self.ds
        g = ds.fwd_net(H, self.cost)
        L1 = np.zeros(ds.n, bool); S1 = np.zeros(ds.n, bool)
        L3 = np.zeros(ds.n, bool); S3 = np.zeros(ds.n, bool)
        ML = np.zeros(ds.n, bool); MS = np.zeros(ds.n, bool)
        found = {}
        for k in range(len(self.folds)):
            tr = self.train_idx(k, H)
            if len(tr) < MIN_TRAIN.get(self.tf, 5000):
                continue
            prims = off.make_primitives(ds.X, self.names, tr, DISCRETE)
            te = self.test_idx(k)
            res = {}
            for d, tgt in ((1, g - self.cost), (-1, -g - self.cost)):
                rules = off.beam_search(ds.X, tgt, tr, prims, H)
                res[d] = rules
                if not rules:
                    continue
                m1 = off.rule_mask(ds.X, rules[0][0], te)
                m3 = np.zeros(len(te), bool)
                for r, _ in rules[:3]:
                    m3 |= off.rule_mask(ds.X, r, te)
                (L1 if d == 1 else S1)[te] = m1
                (L3 if d == 1 else S3)[te] = m3
                if logit_by_fold and k in logit_by_fold:
                    _, _, lsc = logit_by_fold[k]
                    agree = lsc > 0 if d == 1 else lsc < 0
                    (ML if d == 1 else MS)[te] = m1 & agree
            found[k] = {d: [([(self.names[j], op, v) for j, op, v in r], s) for r, s in rr] for d, rr in res.items()}
        self.evaluate(L1, S1, H, "rules", "rules:top1")
        self.evaluate(L3, S3, H, "rules", "rules:top3_or")
        if logit_by_fold:
            self.evaluate(ML, MS, H, "meta", "meta:rule_top1&logit_agree")
        self.artifacts[f"rules_H{H}"] = found

    def run_baselines(self, H):
        ds = self.ds
        rsi = ds.col("rsi").astype(np.float64)
        prev = np.concatenate([[np.nan], rsi[:-1]])
        prev[ds.seg_start] = np.nan
        L = (prev < -0.4) & (rsi >= -0.4)
        S = (prev > 0.4) & (rsi <= 0.4)
        self.evaluate(L, S, H, "baseline", "baseline:rsi_30_70_cross")
        mc = ds.col("macd_cross")
        self.evaluate(mc > 0, mc < 0, H, "baseline", "baseline:macd_cross")
        rng = np.random.default_rng(42)
        r = rng.random(ds.n)
        self.evaluate(r < 0.02, r > 0.98, H, "baseline", "baseline:random_2pct")

    # ------------------------------------------------------------------ main
    def run(self):
        t0 = _time.time()
        sets = model_feature_sets(self.names)
        for H in self.horizons:
            self.log(f"[{self.tf}] H={H} baselines ({_time.time() - t0:.0f}s)")
            self.run_baselines(H)
            logit_last = None
            logit_all_fold = None
            priors = {}
            for set_name, feats in sets.items():
                self.log(f"[{self.tf}] H={H} logit {set_name} ({_time.time() - t0:.0f}s)")
                m, byf = self.run_offline(H, "logit", set_name, feats, C=0.05)
                if set_name == "all":
                    logit_all_fold = byf
            self.log(f"[{self.tf}] H={H} logit all top20 ({_time.time() - t0:.0f}s)")
            # top-20 sparse logit per fold (also the prior of the online learner)
            ds = self.ds
            y = off.binary_target(ds.fwd_ret_atr(H))
            by_fold = {}
            for k in range(len(self.folds)):
                tr = self.train_idx(k, H)
                if len(tr) < MIN_TRAIN.get(self.tf, 5000):
                    continue
                mdl = off.Logit([self.fidx[f] for f in sets["all"]], C=0.05, top_k=20).fit(ds.X, y, tr)
                priors[k] = mdl
                smp = tr[np.random.default_rng(k).choice(len(tr), min(len(tr), 100_000), replace=False)]
                te = self.test_idx(k)
                by_fold[k] = (mdl.score(ds.X, smp), te, mdl.score(ds.X, te))
            self.thresholds_eval(by_fold, H, "logit", "logit:all_top20:C=0.05")
            if priors:
                last = priors[max(priors)]
                self.artifacts[f"logit_top20_H{H}"] = {
                    "features": [self.names[i] for i in last.feat_idx], "w": last.w.tolist(), "b": last.b,
                    "mu": last.std.mu.tolist(), "sd": last.std.sd.tolist()}
            for set_name in ("mom_cdl_vol_div", "all"):
                self.log(f"[{self.tf}] H={H} gbm {set_name} ({_time.time() - t0:.0f}s)")
                m, _ = self.run_offline(H, "gbm", set_name, sets[set_name], n_estimators=80)
                if m is not None and set_name == "all":
                    imp = m.importance()
                    order = np.argsort(-imp)
                    self.artifacts[f"gbm_importance_H{H}"] = [(self.names[m.feat_idx[i]], float(imp[i])) for i in order[:40]]
            for set_name, feats in KNN_SETS.items():
                for k in (8, 16):
                    self.log(f"[{self.tf}] H={H} knn {set_name} k={k} ({_time.time() - t0:.0f}s)")
                    self.run_knn(H, set_name, feats, k=k, step=1 if self.tf == "1w" else 4)
            if priors:
                self.log(f"[{self.tf}] H={H} online logit ({_time.time() - t0:.0f}s)")
                for lr, l2, tag in ((0.002, 0.01, "logit_top20"), (0.01, 0.01, "logit_top20"), (0.01, 0.0, "zero")):
                    self.run_online_logit(H, priors, lr, l2, tag)
            self.log(f"[{self.tf}] H={H} rules + meta ({_time.time() - t0:.0f}s)")
            self.run_rules(H, logit_all_fold)
            self.save()
        self.log(f"[{self.tf}] done in {_time.time() - t0:.0f}s, trials={len(self.trials)}")
        return self.save()

    def save(self):
        df = pd.DataFrame(self.trials)
        df.to_parquet(self.out / f"trials_{self.tf}.parquet", index=False)
        (self.out / f"artifacts_{self.tf}.json").write_text(json.dumps(self.artifacts, indent=1, default=str))
        return df
