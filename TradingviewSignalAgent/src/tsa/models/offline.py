"""Offline (periodically retrained) models: logistic regression, LightGBM, rule search.

All of them are trained only on rows whose label has matured before the test
window starts (purged + embargoed walk-forward), then frozen for that window.
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression

DEADZONE = 0.1  # |fwd move| < 0.1 ATR -> ambiguous, not used for training


def binary_target(fwd_atr: np.ndarray) -> np.ndarray:
    y = np.where(fwd_atr > DEADZONE, 1.0, np.where(fwd_atr < -DEADZONE, 0.0, np.nan))
    y[np.isnan(fwd_atr)] = np.nan
    return y


def _subsample(idx: np.ndarray, max_rows: int, seed: int = 0) -> np.ndarray:
    if len(idx) <= max_rows:
        return idx
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(idx, max_rows, replace=False))


class Standardizer:
    def __init__(self, X: np.ndarray):
        self.mu = X.mean(0).astype(np.float64)
        self.sd = X.std(0).astype(np.float64) + 1e-9

    def __call__(self, X: np.ndarray) -> np.ndarray:
        return np.clip((X - self.mu) / self.sd, -5, 5)


class Logit:
    """(Sparse) logistic regression P(up over H bars). Pine: weighted sum + sigmoid."""

    def __init__(self, feat_idx, C=0.05, top_k=None, max_rows=400_000):
        self.feat_idx = np.asarray(feat_idx)
        self.C, self.top_k, self.max_rows = C, top_k, max_rows

    def fit(self, X, y, idx):
        idx = idx[~np.isnan(y[idx])]
        idx = _subsample(idx, self.max_rows)
        Xf = X[idx][:, self.feat_idx].astype(np.float64)
        self.std = Standardizer(Xf)
        Z = self.std(Xf)
        m = LogisticRegression(C=self.C, max_iter=300)
        m.fit(Z, y[idx])
        if self.top_k and self.top_k < len(self.feat_idx):
            keep = np.argsort(-np.abs(m.coef_[0]))[: self.top_k]
            self.feat_idx = self.feat_idx[np.sort(keep)]
            return self.fit(X, y, idx)
        self.w = m.coef_[0].copy()
        self.b = float(m.intercept_[0])
        return self

    def score(self, X, idx=None):
        Xf = (X if idx is None else X[idx])[:, self.feat_idx].astype(np.float64)
        z = self.std(Xf) @ self.w + self.b
        return 2.0 / (1.0 + np.exp(-z)) - 1.0


class GBM:
    """Shallow LightGBM classifier (exportable to Pine as nested ifs)."""

    def __init__(self, feat_idx, n_estimators=80, num_leaves=7, lr=0.05, max_rows=800_000):
        self.feat_idx = np.asarray(feat_idx)
        self.params = dict(n_estimators=n_estimators, num_leaves=num_leaves, max_depth=3, learning_rate=lr,
                           min_child_samples=1000, subsample=0.5, subsample_freq=1, colsample_bytree=0.6,
                           reg_lambda=1.0, verbose=-1, n_jobs=2)
        self.max_rows = max_rows

    def fit(self, X, y, idx):
        import lightgbm as lgb
        idx = idx[~np.isnan(y[idx])]
        idx = _subsample(idx, self.max_rows)
        self.model = lgb.LGBMClassifier(**self.params)
        self.model.fit(X[idx][:, self.feat_idx], y[idx])
        return self

    def score(self, X, idx=None):
        Xf = (X if idx is None else X[idx])[:, self.feat_idx]
        return 2.0 * self.model.predict_proba(Xf)[:, 1] - 1.0

    def importance(self):
        return self.model.booster_.feature_importance("gain")


# --------------------------------------------------------------------------- rules
QS = (0.05, 0.1, 0.2, 0.8, 0.9, 0.95)


def make_primitives(X: np.ndarray, feat_names: list, idx: np.ndarray, discrete: set):
    """Threshold conditions from *training* quantiles: (feature, op, threshold)."""
    prims = []
    sub = X[_subsample(idx, 200_000, 1)]
    for j, name in enumerate(feat_names):
        col = sub[:, j]
        if name in discrete:
            if (col > 0).any():
                prims.append((j, ">", 0.0 if not name.startswith("div_") else 0.5))
            if (col < 0).any():
                prims.append((j, "<", 0.0))
            continue
        qv = np.quantile(col, QS)
        for q, v in zip(QS, qv):
            prims.append((j, "<=" if q < 0.5 else ">=", float(v)))
    # de-duplicate identical thresholds
    seen, out = set(), []
    for p in prims:
        key = (p[0], p[1], round(p[2], 6))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def prim_mask(X, p, rows=None):
    j, op, v = p
    col = X[:, j] if rows is None else X[rows, j]
    if op == ">":
        return col > v
    if op == "<":
        return col < v
    if op == ">=":
        return col >= v
    return col <= v


def rule_mask(X, rule, rows=None):
    n = X.shape[0] if rows is None else len(rows)
    m = np.ones(n, bool)
    for p in rule:
        m &= prim_mask(X, p, rows)
    return m


def beam_search(X, target, idx, prims, H, depth=3, beam=20, min_support=0.002, max_rows=200_000):
    """Greedy beam search of conjunctions maximising the overlap-adjusted t-stat
    of the net forward return on the *training* rows."""
    idx = idx[~np.isnan(target[idx])]
    idx = _subsample(idx, max_rows, 2)
    t = target[idx]
    n = len(idx)
    min_n = max(int(min_support * n), 30)
    P = np.stack([prim_mask(X, p, idx) for p in prims])  # (m, n) bool

    def score(cnt, s, ss):
        mean = s / np.maximum(cnt, 1)
        var = np.maximum(ss / np.maximum(cnt, 1) - mean ** 2, 1e-12)
        neff = np.maximum(cnt / max(H, 1), 1)
        sc = mean / np.sqrt(var) * np.sqrt(neff)
        sc[(cnt < min_n) | (mean <= 0)] = -np.inf
        return sc

    Pf = P.astype(np.float32)
    cnt = P.sum(1)
    sc = score(cnt, Pf @ t, Pf @ (t * t))
    order = np.argsort(-sc)[:beam]
    frontier = [((int(i),), P[i], sc[i]) for i in order if np.isfinite(sc[i])]
    best = list(frontier)
    for _ in range(depth - 1):
        cand = []
        for rule, m, s0 in frontier:
            rows = np.flatnonzero(m)
            if len(rows) < 2 * min_n:
                continue
            sub = Pf[:, rows]
            tt = t[rows]
            c2 = P[:, rows].sum(1)
            s2 = score(c2, sub @ tt, sub @ (tt * tt))
            for i in np.argsort(-s2)[:beam]:
                if not np.isfinite(s2[i]) or i in rule or s2[i] <= s0:
                    continue
                cand.append((tuple(sorted(rule + (int(i),))), m & P[i], s2[i]))
        uniq = {}
        for r in cand:
            if r[0] not in uniq or uniq[r[0]][2] < r[2]:
                uniq[r[0]] = r
        frontier = sorted(uniq.values(), key=lambda r: -r[2])[:beam]
        best += frontier
        if not frontier:
            break
    best = sorted(best, key=lambda r: -r[2])
    return [([prims[i] for i in r[0]], float(r[2])) for r in best[:5]]
