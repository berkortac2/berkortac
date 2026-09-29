"""Python mirror of the *exported* Pine model (reads final_models.json only).

It recomputes the score exactly as the generated Pine code does (clipped
z-scores, sigmoid, nested tree ternaries, rule conjunctions) so the export can
be checked bar for bar against the research model (scripts/07_parity_check.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _z(x, mu, sd):
    return np.clip((np.nan_to_num(x) - mu) / sd, -5, 5)


def _linear(m, F: pd.DataFrame, w="w", b="b", feats="features", mu="mu", sd="sd"):
    z = np.full(len(F), float(m[b]))
    for f, wi, mi, si in zip(m[feats], m[w], m[mu], m[sd]):
        z += wi * _z(F[f].to_numpy(np.float64), mi, si)
    return z


def _tree(node, F, fnames, idx):
    if "leaf_value" in node:
        return np.full(len(idx), node["leaf_value"])
    x = F[fnames[node["split_feature"]]].to_numpy(np.float64)[idx]
    go_left = x <= node["threshold"]
    out = np.empty(len(idx))
    if go_left.any():
        out[go_left] = _tree(node["left_child"], F, fnames, idx[go_left])
    if (~go_left).any():
        out[~go_left] = _tree(node["right_child"], F, fnames, idx[~go_left])
    return out


def _rules(rules, F):
    m = np.zeros(len(F), bool)
    for rule in rules:
        r = np.ones(len(F), bool)
        for f, op, v in rule:
            x = F[f].to_numpy(np.float64)
            r &= {"<=": x <= v, ">=": x >= v, ">": x > v, "<": x < v}[op]
        m |= r
    return m


def score(m: dict, F: pd.DataFrame) -> np.ndarray:
    fam = m["family"]
    if fam == "logit":
        return 2.0 / (1.0 + np.exp(-_linear(m, F))) - 1.0
    if fam == "gbm":
        idx = np.arange(len(F))
        raw = sum(_tree(t["tree_structure"], F, m["features"], idx) for t in m["trees"])
        return 2.0 / (1.0 + np.exp(-raw)) - 1.0
    if fam in ("rules", "meta"):
        L = _rules(m["rules"]["1"], F)
        S = _rules(m["rules"]["-1"], F)
        if fam == "meta":
            z = _linear(m, F, "meta_w", "meta_b", "meta_features", "meta_mu", "meta_sd")
            L &= z > 0
            S &= z < 0
        return np.where(L, 1.0, np.where(S, -1.0, 0.0))
    raise NotImplementedError(f"{fam} is stateful (online); checked through the replay engine instead")


def signals(m: dict, F: pd.DataFrame):
    sc = score(m, F)
    if m["family"] in ("rules", "meta"):
        return sc > 0, sc < 0
    return (sc >= m["thr_long"]) & (sc > 0), (sc <= m["thr_short"]) & (sc < 0)
