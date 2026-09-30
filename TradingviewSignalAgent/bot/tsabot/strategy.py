"""The 5m signal model: exactly the research feature code + frozen rules."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from tsa.features.registry import compute_features
from tsa.indicators import pine_ta as ta

MIN_BARS = 600          # history needed for the slowest feature (EMA200 / 100-bar z-scores) to settle
OPS = {"<=": np.less_equal, ">=": np.greater_equal, "<": np.less, ">": np.greater}


@dataclass
class DirModel:
    rules: list = field(default_factory=list)   # OR of AND-conjunctions [(feature, op, value), ...]
    H: int = 24                                  # time exit after H bars
    tp_atr: float | None = None
    sl_atr: float | None = None
    exit_rules: list = field(default_factory=list)   # indicator exit ("ÇIK"), checked on every closed bar

    @staticmethod
    def _first(rules, row: dict) -> int:
        for i, rule in enumerate(rules):
            if all(bool(OPS[op](row.get(f, 0.0), v)) for f, op, v in rule):
                return i
        return -1

    def match(self, row: dict) -> int:
        """Index of the first matching entry rule or -1."""
        return self._first(self.rules, row)

    def should_exit(self, row: dict) -> bool:
        return bool(self.exit_rules) and self._first(self.exit_rules, row) >= 0


@dataclass
class Signal:
    symbol: str
    bar_time: int
    direction: int      # +1 AL (long), -1 SAT (short), 0 none
    rule: int
    close: float
    atr: float
    features: dict


class Strategy:
    def __init__(self, model: dict):
        self.model = model
        def mk(spec):
            spec = spec or {}
            d = DirModel(rules=spec.get("rules") or [], H=int(spec.get("H", 24)), tp_atr=spec.get("tp_atr"),
                         sl_atr=spec.get("sl_atr"), exit_rules=spec.get("exit_rules") or [])
            d.rules = [[tuple(c) for c in r] for r in d.rules]
            d.exit_rules = [[tuple(c) for c in r] for r in d.exit_rules]
            return d
        self.long, self.short = mk(model.get("long")), mk(model.get("short"))
        self.features_used = sorted({c[0] for d in (self.long, self.short) for r in d.rules + d.exit_rules
                                     for c in r})

    @classmethod
    def load(cls, path: Path) -> "Strategy":
        return cls(json.loads(Path(path).read_text()))

    def params(self, direction: int) -> DirModel:
        return self.long if direction > 0 else self.short

    def evaluate(self, symbol: str, k5: pd.DataFrame, k1h: pd.DataFrame | None,
                 allow_long: bool = True, allow_short: bool = True) -> Signal | None:
        """k5: CLOSED 5m klines (oldest first). k1h: 1h klines INCLUDING the running hour
        (its values are never used, it only anchors 'previous closed hour' exactly like
        Pine's request.security(expr[1], lookahead_on))."""
        if len(k5) < MIN_BARS:
            return None
        k5 = k5.reset_index(drop=True)
        f = compute_features(k5, "5m", k1h.reset_index(drop=True) if k1h is not None else None)
        row = {k: (0.0 if not np.isfinite(v) else float(v)) for k, v in f.iloc[-1].items()}
        atr = float(ta.atr(k5["high"].to_numpy(), k5["low"].to_numpy(), k5["close"].to_numpy(), 14)[-1])
        d, r = 0, -1
        if allow_long and (r := self.long.match(row)) >= 0:
            d = 1
        elif allow_short and (r := self.short.match(row)) >= 0:
            d = -1
        return Signal(symbol, int(k5["open_time"].iloc[-1]), d, r, float(k5["close"].iloc[-1]), atr,
                      {k: row.get(k, 0.0) for k in self.features_used})

    def describe(self) -> dict:
        def one(d: DirModel):
            return {"rules": [[list(c) for c in r] for r in d.rules], "H": d.H, "tp_atr": d.tp_atr,
                    "sl_atr": d.sl_atr, "exit_rules": [[list(c) for c in r] for r in d.exit_rules]}
        return {"name": self.model.get("name", "5m model"), "long": one(self.long), "short": one(self.short),
                "stats": self.model.get("stats", {}), "cost_rt": self.model.get("cost_rt")}
