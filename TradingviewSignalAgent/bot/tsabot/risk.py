"""Budget cap, position sizing and loss limits."""
from __future__ import annotations

from .config import Settings
from .exchange.binance import SymbolRules


class Risk:
    def __init__(self, s: Settings):
        self.s = s

    def effective_budget(self, realized_total: float) -> float:
        """Budget the bot may commit as margin. Losses always shrink it; profits are
        reused only with `compound`. It never exceeds budget + profits."""
        b = self.s.budget_usdt + (realized_total if self.s.compound else min(realized_total, 0.0))
        return max(b, 0.0)

    def margin_per_position(self, realized_total: float) -> float:
        return self.effective_budget(realized_total) / self.s.max_positions

    def stop_reason(self, realized_total: float) -> str | None:
        if realized_total <= -self.s.max_drawdown_pct / 100 * self.s.budget_usdt:
            return (f"Maksimum zarar limiti aşıldı ({realized_total:.2f} USDT ≤ -%{self.s.max_drawdown_pct} bütçe). "
                    "Bot durduruldu.")
        return None

    def can_open(self, open_margins: list[float], realized_total: float, realized_today: float,
                 available: float | None) -> tuple[bool, str]:
        if self.stop_reason(realized_total):
            return False, "max_drawdown"
        if realized_today <= -self.s.daily_loss_limit_pct / 100 * self.s.budget_usdt:
            return False, "Günlük zarar limiti doldu; yarın (UTC) yeni işlem açılacak"
        if len(open_margins) >= self.s.max_positions:
            return False, "Maksimum eş zamanlı pozisyon sayısında"
        mpp = self.margin_per_position(realized_total)
        if sum(open_margins) + mpp > self.effective_budget(realized_total) + 1e-9:
            return False, "Bütçe dolu"
        if available is not None and mpp > available:
            return False, f"Borsadaki kullanılabilir bakiye ({available:.2f} USDT) yetersiz"
        return True, ""

    def size(self, price: float, rules: SymbolRules, realized_total: float) -> tuple[float, float]:
        """(qty, margin). qty = 0 when the per-position budget is below the exchange minimum."""
        mpp = self.margin_per_position(realized_total)
        qty = rules.floor_qty(mpp * self.s.leverage / price)
        if qty <= 0 or qty * price < rules.min_notional:
            return 0.0, 0.0
        return qty, qty * price / self.s.leverage
