"""Order execution: simulated (paper/replay) and Binance USDT-M futures (testnet/live)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .exchange.binance import BinanceFutures, ExchangeError, SymbolRules

log = logging.getLogger("tsabot.broker")
TAKER_FEE = 0.0005
SLIPPAGE = 0.0002


@dataclass
class Fill:
    price: float
    qty: float
    fee: float          # USDT


class PaperBroker:
    """Simulated fills: taker fee 0.05% + 0.02% slippage per side (same as the research)."""
    simulated = True

    def __init__(self, fee: float = TAKER_FEE, slippage: float = SLIPPAGE):
        self.fee, self.slip = fee, slippage

    async def prepare(self, symbols, leverage, own=()) -> list[str]:
        return []

    async def position_of(self, symbol) -> tuple[float, float | None]:
        return 0.0, None

    async def available_usdt(self) -> float | None:
        return None      # only the budget limits paper trading

    async def open(self, symbol: str, direction: int, qty: float, ref_price: float, rules: SymbolRules) -> Fill:
        px = ref_price * (1 + direction * self.slip)
        return Fill(px, qty, px * qty * self.fee)

    async def close(self, symbol: str, direction: int, qty: float, ref_price: float, slip: bool = True) -> Fill:
        px = ref_price * (1 - direction * self.slip) if slip else ref_price
        return Fill(px, qty, px * qty * self.fee)

    async def set_brackets(self, symbol, direction, tp, sl, rules):
        return None

    async def cancel_brackets(self, symbol):
        return None

    async def closed_by_exchange(self, open_syms: dict) -> dict:
        return {}


class BinanceBroker:
    """Real orders. Positions are one-way, isolated margin, bot-opened only."""
    simulated = False

    def __init__(self, client: BinanceFutures):
        self.client = client
        self.external: set[str] = set()
        self.prepared: set[str] = set()

    async def prepare(self, symbols, leverage, own=()) -> list[str]:
        """Checks account mode; returns warnings. Symbols with a position the bot did not open
        (not in its own persisted positions) are skipped."""
        warns = []
        if await self.client.dual_side():
            raise ExchangeError(0, None, "Hesap Hedge Mode'da. Binance Futures ayarlarından One-way Mode'a geç.")
        for p in await self.client.positions():
            if float(p.get("positionAmt", 0)) != 0 and p["symbol"] in symbols and p["symbol"] not in own:
                self.external.add(p["symbol"])
        if self.external:
            warns.append(f"Elle açılmış pozisyonlar olduğu için atlanıyor: {sorted(self.external)}")
        self.leverage = leverage
        return warns

    async def available_usdt(self) -> float | None:
        acc = await self.client.account()
        return float(acc.get("availableBalance", 0.0))

    async def _prep_symbol(self, symbol):
        if symbol not in self.prepared:
            await self.client.set_isolated(symbol)
            await self.client.set_leverage(symbol, self.leverage)
            self.prepared.add(symbol)

    async def open(self, symbol, direction, qty, ref_price, rules) -> Fill:
        await self._prep_symbol(symbol)
        # a stop left over from an earlier position (e.g. its cancel failed) must not close the new one
        await self.cancel_brackets(symbol)
        r = await self.client.market_order(symbol, "BUY" if direction > 0 else "SELL", qty,
                                           client_id=f"tsa{int(time.time() * 1000)}")
        return await self._filled(symbol, r, ref_price)

    async def close(self, symbol, direction, qty, ref_price, slip=True) -> Fill:
        # close FIRST, then remove the exchange stop: if the market order fails the position keeps its stop
        side = "SELL" if direction > 0 else "BUY"
        r = await self.client.market_order(symbol, side, qty, reduce_only=True,
                                           client_id=f"tsx{int(time.time() * 1000)}")
        fill = await self._filled(symbol, r, ref_price)
        if fill.qty < qty * 0.999:                  # partial fill: close what is really left
            amt, _ = await self.position_of(symbol)
            if amt != 0.0:
                r2 = await self.client.market_order(symbol, side, abs(amt), reduce_only=True,
                                                    client_id=f"tsy{int(time.time() * 1000)}")
                f2 = await self._filled(symbol, r2, ref_price)
                q = fill.qty + f2.qty
                fill = Fill((fill.price * fill.qty + f2.price * f2.qty) / q, q, fill.fee + f2.fee)
        await self.cancel_brackets(symbol)
        return fill

    async def _filled(self, symbol, r: dict, ref_price: float) -> Fill:
        """Fill of a MARKET order; waits briefly if the exchange has not reported it as final yet.
        Raises when nothing was executed (the caller then keeps / reconciles the position)."""
        final = ("FILLED", "EXPIRED", "CANCELED", "REJECTED", "EXPIRED_IN_MATCH")
        for _ in range(3):
            if r.get("status") in final or r.get("orderId") is None:
                break
            await asyncio.sleep(0.5)
            r = await self.client.order(symbol, r["orderId"])
        q = float(r.get("executedQty") or 0)
        if q <= 0:
            raise ExchangeError(0, None, f"{symbol}: piyasa emri dolmadı (durum {r.get('status')})")
        px = float(r.get("avgPrice") or 0) or ref_price
        return Fill(px, q, px * q * TAKER_FEE)

    async def set_brackets(self, symbol, direction, tp, sl, rules):
        side = "SELL" if direction > 0 else "BUY"
        if sl:
            await self.client.conditional_close(symbol, side, "STOP_MARKET", rules.round_price(sl))
        if tp:
            await self.client.conditional_close(symbol, side, "TAKE_PROFIT_MARKET", rules.round_price(tp))

    async def cancel_brackets(self, symbol):
        await self.client.cancel_conditionals(symbol)

    async def position_of(self, symbol) -> tuple[float, float | None]:
        """(signed position amount, entry price) of one symbol on the exchange."""
        for p in await self.client.positions(symbol):
            if p.get("symbol") == symbol:
                return float(p.get("positionAmt", 0)), float(p.get("entryPrice", 0)) or None
        return 0.0, None

    async def closed_by_exchange(self, open_syms: dict) -> dict:
        """{symbol: Fill} for bot positions that the exchange closed (TP/SL/liquidation).
        A position is only declared closed after a per-symbol re-check shows it flat."""
        if not open_syms:
            return {}
        amt = {p["symbol"]: float(p.get("positionAmt", 0)) for p in await self.client.positions()}
        out = {}
        for sym, pos in open_syms.items():
            if amt.get(sym, 0.0) != 0.0:
                continue
            a, _ = await self.position_of(sym)          # confirm: never act on an incomplete list
            if a != 0.0:
                continue
            trades = await self.client.user_trades(sym, pos.entry_time_ms)
            closing = [t for t in trades if (t["side"] == "SELL") == (pos.direction > 0)]
            if closing:
                q = sum(float(t["qty"]) for t in closing)
                px = sum(float(t["price"]) * float(t["qty"]) for t in closing) / q
                # commission may be paid in BNB (fee discount): only USDT amounts can be added as USDT
                fee = sum(float(t.get("commission", 0)) if t.get("commissionAsset", "USDT") == "USDT"
                          else float(t["price"]) * float(t["qty"]) * TAKER_FEE for t in closing)
            else:
                q, px, fee = pos.qty, await self.client.price(sym), pos.qty * pos.entry_price * TAKER_FEE
                log.warning("%s closed on exchange but no closing fills found; using last price", sym)
            await self.cancel_brackets(sym)
            out[sym] = Fill(px, q, fee)
        return out
