"""Bot settings (validated) and paths."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import BOT_ROOT
from .secrets import write_private

DATA_DIR = BOT_ROOT / "data"
MODEL_PATH = BOT_ROOT / "model" / "model_5m.json"
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")
MODES = ("paper", "testnet", "live", "replay")

# Liquid USDT-M perpetuals with out-of-sample evidence in the research
# (20 training coins + 6 unseen + 10 second hold-out coins).
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "NEARUSDT", "UNIUSDT", "ADAUSDT",
    "LINKUSDT", "AVAXUSDT", "BCHUSDT", "AAVEUSDT", "FILUSDT", "LTCUSDT", "XLMUSDT", "DOTUSDT",
    "1000SHIBUSDT", "TRXUSDT", "HBARUSDT", "1000PEPEUSDT", "SUIUSDT", "ENAUSDT", "WLDUSDT", "TAOUSDT",
    "ARBUSDT", "ONDOUSDT", "INJUSDT", "FETUSDT", "APTUSDT", "ETCUSDT", "OPUSDT", "CRVUSDT", "TIAUSDT",
    "ICPUSDT", "WIFUSDT"]

LIMITS = {"budget_usdt": (5.0, 1_000_000.0), "max_positions": (1, 20), "leverage": (1, 10),
          "daily_loss_limit_pct": (0.5, 100.0), "max_drawdown_pct": (1.0, 100.0),
          "emergency_stop_pct": (1.0, 50.0), "replay_speed": (0.0, 10.0)}


@dataclass
class Settings:
    mode: str = "paper"                 # paper | testnet | live | replay (offline demo)
    budget_usdt: float = 100.0          # hard cap on total margin the bot may use
    max_positions: int = 8              # concurrent positions; margin per position = budget / max_positions
    leverage: int = 1                   # 1x: portfolio simulation showed >50% drawdowns at 2x in 2025
    allow_long: bool = True
    allow_short: bool = True
    daily_loss_limit_pct: float = 5.0   # of budget; no new entries for the rest of the UTC day
    max_drawdown_pct: float = 20.0      # of budget; bot stops itself
    emergency_stop_pct: float = 8.0     # exchange-side stop for time-exit-only trades (bot offline protection)
    compound: bool = False              # reuse realised profit (never beyond budget when losing)
    symbols: list = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    live_confirmed: bool = False        # must be ticked in the UI before live trading
    replay_speed: float = 0.05          # seconds per 5m bar in replay mode

    def validate(self) -> list[str]:
        err = []
        if self.mode not in MODES:
            err.append(f"mode must be one of {MODES}")
        for k, (lo, hi) in LIMITS.items():
            v = getattr(self, k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not (lo <= v <= hi):
                err.append(f"{k} must be between {lo} and {hi}")
        if not isinstance(self.symbols, list) or not (1 <= len(self.symbols) <= 60):
            err.append("symbols: 1-60 symbols")
        else:
            bad = [s for s in self.symbols if not isinstance(s, str) or not SYMBOL_RE.match(s)]
            if bad:
                err.append(f"only USDT perpetual symbols like BTCUSDT are allowed: {bad[:5]}")
        for k in ("allow_long", "allow_short", "compound", "live_confirmed"):
            if not isinstance(getattr(self, k), bool):
                err.append(f"{k} must be true/false")
        for k in ("max_positions", "leverage"):
            if not isinstance(getattr(self, k), int) or isinstance(getattr(self, k), bool):
                err.append(f"{k} must be a whole number")
        if not (self.allow_long or self.allow_short):
            err.append("enable at least one direction")
        if (isinstance(self.leverage, int) and not isinstance(self.leverage, bool) and self.leverage >= 1
                and isinstance(self.emergency_stop_pct, (int, float))
                and self.emergency_stop_pct >= 100 / self.leverage - 1):
            # an isolated position is liquidated near -100/leverage %: the stop must come first
            err.append(f"emergency_stop_pct must be below {100 / self.leverage - 1:.1f} at {self.leverage}x "
                       "(otherwise liquidation comes before the stop)")
        return err

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        s = cls(**{**asdict(cls()), **known})
        if isinstance(s.symbols, list):
            s.symbols = list(dict.fromkeys(x.strip().upper() for x in s.symbols if isinstance(x, str)))
        for k in ("max_positions", "leverage"):
            if isinstance(getattr(s, k), float) and getattr(s, k).is_integer():
                setattr(s, k, int(getattr(s, k)))
        return s


def load_settings(path: Path | None = None) -> Settings:
    p = path or DATA_DIR / "settings.json"
    if p.exists():
        s = Settings.from_dict(json.loads(p.read_text()))
        if not s.validate():
            return s
    return Settings()


def save_settings(s: Settings, path: Path | None = None) -> None:
    write_private(path or DATA_DIR / "settings.json", json.dumps(asdict(s), indent=1))
