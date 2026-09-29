"""Deep 5-minute search (second round).

Differences to the first-round runner:
* much longer history: USDT-M perps from 2020-01 plus Binance *spot* 2017-08..2019-12
  (2018 bear market) as separate, never-stitched segments -> training only;
* long and short rules are searched AND evaluated separately (a SAT rule is judged
  on its own trades, not only when no AL signal blocks it);
* denser threshold grid, deeper/wider beam, two support levels -> "frequent"
  variants next to the high-precision ones;
* extra barriers (time exit + stop-loss only) that a bot can execute directly;
* memory-lean trade outcome cache; heavy online learners are skipped (they lost
  in round one).
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path

import numpy as np
import pandas as pd

from ..dataset import final_cutoff, load_cfg, load_dataset, load_universe, tf_ms
from ..eval.metrics import trade_metrics
from ..labels import simulate, trade_outcomes
from ..models import offline as off
from .runner import DISCRETE, make_folds, model_feature_sets

TF = "5m"
FEATURE_DIR = "data/features_ext"
SPOT_SEGMENTS = ["BTCUSDT@spot", "ETHUSDT@spot", "BNBUSDT@spot", "XRPUSDT@spot", "ADAUSDT@spot", "LTCUSDT@spot"]
HORIZONS = [6, 12, 24, 48]
BARRIERS = [(None, None), (None, 1.5), (None, 2.5), (1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (1.5, 1.5),
            (2.0, 2.0), (3.0, 1.5), (3.0, 3.0)]
RULE_QS = (0.02, 0.05, 0.1, 0.2, 0.3, 0.7, 0.8, 0.9, 0.95, 0.98)
RULE_VARIANTS = [dict(depth=3, min_support=0.002), dict(depth=3, min_support=0.01),
                 dict(depth=4, min_support=0.002), dict(depth=4, min_support=0.01)]
BEAM = 40
RULE_ROWS = 400_000
QS = [0.005, 0.01, 0.02, 0.05, 0.10]
MIN_TRADES = 300
BARS_PER_DAY = 288


def variant_tag(v: dict) -> str:
    return f"d{v['depth']}s{v['min_support']}"


class DeepSearch5m:
    def __init__(self, out_dir: Path, log=print, horizons=None):
        self.cfg = load_cfg()
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = log
        uni = load_universe()
        self.cutoff = final_cutoff(TF)
        self.symbols = uni["search5m"] + SPOT_SEGMENTS
        self.ds = load_dataset(TF, self.symbols, t_max=self.cutoff, feature_dir=FEATURE_DIR)
        self.horizons = horizons or HORIZONS
        self.cost = 2 * (self.cfg["fee_per_side"] + self.cfg["slippage_per_side"])
        # test folds only on perp rows (spot segments end 2019-12 and are training-only)
        self.folds = make_folds(self.ds.time, self.cfg["walkforward"]["n_folds"])
        self.embargo = self.cfg["walkforward"]["embargo_bars"]
        self.names = self.ds.feat_names
        self.fidx = {n: i for i, n in enumerate(self.names)}
        self.fold_id = np.full(self.ds.n, -1, np.int8)
        perp = np.array(["@spot" not in s for s in self.ds.symbols])[self.ds.coin]
        for k, (a, b) in enumerate(self.folds):
            self.fold_id[(self.ds.time >= a) & (self.ds.time < b) & perp] = k
        self.test_mask = self.fold_id >= 0
        self.n_test_bars = int(self.test_mask.sum())
        self.n_test_coins = len([s for s in self.ds.symbols if "@spot" not in s])
        self.trials: list[dict] = []
        self.artifacts: dict = {}
        self._out = {}
        log(f"[deep5m] rows={self.ds.n:,} segs={len(self.ds.symbols)} test_rows={self.n_test_bars:,} "
            f"folds={[(str(pd.Timestamp(a, unit='ms').date()), str(pd.Timestamp(b, unit='ms').date())) for a, b in self.folds]}")

    # ------------------------------------------------------------ helpers
    def train_idx(self, k, H):
        lim = self.folds[k][0] - (H + 1 + self.embargo) * tf_ms(TF)
        return np.flatnonzero(self.ds.time < lim)

    def test_idx(self, k):
        return np.flatnonzero(self.fold_id == k)

    def outcomes(self, H, a, b, d):
        key = (H, a, b, d)
        if key not in self._out:
            ds = self.ds
            aa = 1e6 if a is None else a
            bb = 1e6 if b is None else b
            net = np.full(ds.n, np.nan, np.float32)
            ex = np.full(ds.n, -1, np.int32)
            hit = np.full(ds.n, np.nan, np.float32)
            for s, e in zip(ds.seg_start, ds.seg_end):
                if not self.test_mask[s:e].any():
                    continue
                n_, x_, h_ = trade_outcomes(ds.o[s:e], ds.h[s:e], ds.l[s:e], ds.c[s:e], ds.atr[s:e],
                                            H, aa, bb, self.cost, d)
                net[s:e], hit[s:e] = n_, h_
                ex[s:e] = np.where(x_ >= 0, x_ + s, -1)
            self._out[key] = (net, ex, hit)
        return self._out[key]

    def evaluate_all(self, signals: dict, H: int):
        """Barrier-outer loop: only one barrier's outcome arrays live in memory at a time."""
        live = {}
        for name, (L, S) in signals.items():
            L = L & self.test_mask
            S = S & self.test_mask
            if L.any() or S.any():
                live[name] = (L, S)
        for a, b in BARRIERS:
            self._out.clear()
            for name, (L, S) in live.items():
                self.evaluate(L, S, H, name.split(":")[0], name, barriers=[(a, b)])
        self._out.clear()

    def evaluate(self, L, S, H, family, config, extra=None, barriers=None):
        ds = self.ds
        L = L & self.test_mask
        S = S & self.test_mask
        if L.sum() + S.sum() == 0:
            return
        days = self.n_test_bars / BARS_PER_DAY
        for a, b in (barriers or BARRIERS):
            nl, el, hl = self.outcomes(H, a, b, 1)
            ns, es, hs = self.outcomes(H, a, b, -1)
            rows, dirs, nets, hits = simulate(L, S, nl, el, hl, ns, es, hs, ds.seg_start, ds.seg_end)
            m = trade_metrics(dirs, nets, hits, self.n_test_bars, 105120, self.n_test_coins)
            fm = []
            for k in range(len(self.folds)):
                sel = self.fold_id[rows] == k
                fm.append(float(nets[sel].mean()) if sel.sum() >= 10 else float("nan"))
            pos = float(np.nansum(np.array(fm) > 0) / len(self.folds))
            ok = m["trades"] >= MIN_TRADES
            row = {"tf": TF, "family": family, "config": config, "H": H, "tp_atr": a, "sl_atr": b,
                   "score": (m.get("tstat", 0.0) * pos) if ok else -99.0, "pos_folds": pos,
                   "trades_per_coin_day": m["trades"] / days,   # days are summed over coins
                   "net_per_coin_day": float(nets.sum()) / days if len(nets) else 0.0,
                   **{f"fold{k}_avg_net": v for k, v in enumerate(fm)}, **m}
            if extra:
                row.update(extra)
            self.trials.append(row)

    # ------------------------------------------------------------ signal makers
    def baselines(self):
        ds = self.ds
        rsi = ds.col("rsi").astype(np.float64)
        prev = np.concatenate([[np.nan], rsi[:-1]])
        prev[ds.seg_start] = np.nan
        mc = ds.col("macd_cross")
        r = np.random.default_rng(42).random(ds.n)
        return {"baseline:rsi_30_70_cross": ((prev < -0.4) & (rsi >= -0.4), (prev > 0.4) & (rsi <= 0.4)),
                "baseline:macd_cross": (mc > 0, mc < 0),
                "baseline:random_2pct": (r < 0.02, r > 0.98)}

    def model_scores(self, H, kind, feats, **kw):
        ds = self.ds
        y = off.binary_target(ds.fwd_ret_atr(H))
        fi = [self.fidx[f] for f in feats]
        by_fold = {}
        last = None
        for k in range(len(self.folds)):
            tr = self.train_idx(k, H)
            model = off.Logit(fi, **kw) if kind == "logit" else off.GBM(fi, **kw)
            model.fit(ds.X, y, tr)
            smp = tr[np.random.default_rng(k).choice(len(tr), min(len(tr), 100_000), replace=False)]
            te = self.test_idx(k)
            by_fold[k] = (model.score(ds.X, smp), te, model.score(ds.X, te))
            last = model
        return by_fold, last

    def threshold_signals(self, by_fold, prefix):
        out = {}
        for q in QS:
            L = np.zeros(self.ds.n, bool)
            S = np.zeros(self.ds.n, bool)
            for k, (trs, te, sc) in by_fold.items():
                hi = max(np.quantile(trs, 1 - q), 1e-9)
                lo = min(np.quantile(trs, q), -1e-9)
                L[te] = sc >= hi
                S[te] = sc <= lo
            out[f"{prefix}:q={q}"] = (L, S)
        return out

    def rule_signals(self, H, v, agree=None):
        """Walk-forward rule search for one variant; returns signal sets for long-only,
        short-only and combined use (top1 / top3 OR / top5 OR) and rule artifacts."""
        ds = self.ds
        g = ds.fwd_net(H, self.cost)
        n = ds.n
        tag = variant_tag(v)
        sig = {(d, top): np.zeros(n, bool) for d in (1, -1) for top in (1, 3, 5)}
        meta = {d: np.zeros(n, bool) for d in (1, -1)}
        found = {}
        for k in range(len(self.folds)):
            tr = self.train_idx(k, H)
            prims = off.make_primitives(ds.X, self.names, tr, DISCRETE, qs=RULE_QS)
            te = self.test_idx(k)
            found[k] = {}
            for d, tgt in ((1, g - self.cost), (-1, -g - self.cost)):
                rules = off.beam_search(ds.X, tgt, tr, prims, H, depth=v["depth"], beam=BEAM,
                                        min_support=v["min_support"], max_rows=RULE_ROWS)
                found[k][d] = [([(self.names[j], op, val) for j, op, val in r], s) for r, s in rules]
                if not rules:
                    continue
                masks = [off.rule_mask(ds.X, r, te) for r, _ in rules[:5]]
                for top in (1, 3, 5):
                    sig[(d, top)][te] = np.logical_or.reduce(masks[:top])
                if agree is not None and k in agree:
                    lsc = agree[k][2]
                    meta[d][te] = masks[0] & (lsc > 0 if d == 1 else lsc < 0)
        z = np.zeros(n, bool)
        out = {}
        for top in (1, 3, 5):
            out[f"rules:{tag}:long:top{top}"] = (sig[(1, top)], z)
            out[f"rules:{tag}:short:top{top}"] = (z, sig[(-1, top)])
        for top in (1, 3):
            out[f"rules:{tag}:both:top{top}"] = (sig[(1, top)], sig[(-1, top)])
        if agree is not None:
            out[f"meta:{tag}:long"] = (meta[1], z)
            out[f"meta:{tag}:short"] = (z, meta[-1])
            out[f"meta:{tag}:both"] = (meta[1], meta[-1])
        return out, found

    # ------------------------------------------------------------ main
    def run(self):
        t0 = _time.time()
        sets = model_feature_sets(self.names)
        for H in self.horizons:
            self._out.clear()
            self.ds.cache.clear()
            signals = dict(self.baselines())
            self.log(f"[deep5m] H={H} logit ({_time.time() - t0:.0f}s)")
            byf_all, _ = self.model_scores(H, "logit", sets["all"], C=0.05)
            signals.update(self.threshold_signals(byf_all, "logit:all"))
            byf20, last20 = self.model_scores(H, "logit", sets["all"], C=0.05, top_k=20)
            signals.update(self.threshold_signals(byf20, "logit:all_top20"))
            self.artifacts[f"logit_top20_H{H}"] = {"features": [self.names[i] for i in last20.feat_idx],
                                                    "w": last20.w.tolist(), "b": last20.b}
            self.log(f"[deep5m] H={H} gbm ({_time.time() - t0:.0f}s)")
            byf_g, lg = self.model_scores(H, "gbm", sets["all"], n_estimators=120)
            signals.update(self.threshold_signals(byf_g, "gbm:all"))
            imp = lg.importance()
            self.artifacts[f"gbm_importance_H{H}"] = [(self.names[lg.feat_idx[i]], float(imp[i]))
                                                      for i in np.argsort(-imp)[:40]]
            for v in RULE_VARIANTS:
                self.log(f"[deep5m] H={H} rules {variant_tag(v)} ({_time.time() - t0:.0f}s)")
                sg, found = self.rule_signals(H, v, agree=byf_all)
                signals.update(sg)
                self.artifacts[f"rules_H{H}_{variant_tag(v)}"] = found
            self.log(f"[deep5m] H={H} evaluating {len(signals)} signal sets x {len(BARRIERS)} barriers "
                     f"({_time.time() - t0:.0f}s)")
            del byf_all, byf20, byf_g
            self.evaluate_all(signals, H)
            del signals
            self.save()
        self.log(f"[deep5m] done {_time.time() - t0:.0f}s trials={len(self.trials)}")
        return self.save()

    def save(self):
        df = pd.DataFrame(self.trials)
        tp = self.out / "trials_5m_deep.parquet"
        if tp.exists():
            old = pd.read_parquet(tp)
            old = old[~old.H.isin(self.horizons)]
            df = pd.concat([old, df], ignore_index=True)
        df.to_parquet(tp, index=False)
        ap = self.out / "artifacts_5m_deep.json"
        arts = json.loads(ap.read_text()) if ap.exists() else {}
        arts.update(self.artifacts)
        ap.write_text(json.dumps(arts, indent=1, default=str))
        return df
