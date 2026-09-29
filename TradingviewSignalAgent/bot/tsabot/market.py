"""Market data sources: live Binance (public endpoints) and an offline replay of stored candles."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pandas as pd

from . import REPO_ROOT
from .exchange.binance import BinanceFutures, SymbolRules, parse_rules

BAR_MS = 300_000
HOUR_MS = 3_600_000
KEEP_5M = 1200
KEEP_1H = 400


class LiveMarket:
    """Keeps rolling 5m/1h buffers per symbol from Binance public klines."""

    def __init__(self, client: BinanceFutures, delay_s: float = 3.0):
        self.client = client
        self.delay_s = delay_s
        self.k5: dict[str, pd.DataFrame] = {}
        self.k1h: dict[str, pd.DataFrame] = {}

    def now_ms(self) -> int:
        return int(time.time() * 1000) + self.client.offset_ms

    async def rules(self) -> dict[str, SymbolRules]:
        await self.client.sync_time()
        return await self.client.exchange_info()

    async def wait_next_close(self, stop: asyncio.Event) -> None:
        now = self.now_ms()
        nxt = (now // BAR_MS + 1) * BAR_MS
        try:
            await asyncio.wait_for(stop.wait(), timeout=(nxt - now) / 1000 + self.delay_s)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _merge(old: pd.DataFrame | None, new: pd.DataFrame, keep: int) -> pd.DataFrame:
        df = new if old is None else pd.concat([old, new])
        return df.drop_duplicates("open_time", keep="last").sort_values("open_time").tail(keep).reset_index(drop=True)

    async def update(self, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        first = symbol not in self.k5
        n5, n1 = (1000, 300) if first else (5, 3)
        k5 = await self.client.klines(symbol, "5m", n5)
        k1 = await self.client.klines(symbol, "1h", n1)
        now = self.now_ms()
        k5 = k5[k5["close_time"] < now]            # closed 5m bars only
        self.k5[symbol] = self._merge(self.k5.get(symbol), k5, KEEP_5M)
        self.k1h[symbol] = self._merge(self.k1h.get(symbol), k1, KEEP_1H)  # running hour kept (anchor)
        return self.k5[symbol], self.k1h[symbol]

    async def price(self, symbol: str) -> float:
        return await self.client.price(symbol)


class ReplayMarket:
    """Offline demo / test source: replays stored Binance candles bar by bar.

    Entry fills use the NEXT bar's open, exactly like the research backtest."""

    def __init__(self, symbols: list[str], start_ms: int | None = None, speed_s: float = 0.05,
                 raw_dir: Path | None = None, bars: int | None = None):
        raw = raw_dir or REPO_ROOT / "data" / "raw"
        self.d5, self.d1 = {}, {}
        for s in symbols:
            p5, p1 = Path(raw) / f"{s}_5m.parquet", Path(raw) / f"{s}_1h.parquet"
            if p5.exists() and p1.exists():
                self.d5[s] = pd.read_parquet(p5, columns=["open_time", "open", "high", "low", "close", "volume",
                                                          "close_time"])
                self.d1[s] = pd.read_parquet(p1, columns=["open_time", "open", "high", "low", "close", "volume",
                                                          "close_time"])
        self.symbols = list(self.d5)
        last = min(int(d.open_time.iloc[-1]) for d in self.d5.values()) if self.d5 else 0
        self.t = start_ms if start_ms is not None else last - 3 * 86_400_000
        self.t -= self.t % BAR_MS
        self.end = last if bars is None else min(last, self.t + bars * BAR_MS)
        self.speed_s = speed_s
        self.finished = False

    def now_ms(self) -> int:
        return self.t + BAR_MS          # the bar opening at self.t has just closed

    async def rules(self) -> dict[str, SymbolRules]:
        info = {"symbols": [{"symbol": s, "contractType": "PERPETUAL", "quoteAsset": "USDT", "marginAsset": "USDT",
                             "status": "TRADING", "filters": [
                                 {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000000"},
                                 {"filterType": "PRICE_FILTER", "tickSize": "0.0001"},
                                 {"filterType": "MIN_NOTIONAL", "notional": "5"}]} for s in self.symbols]}
        return parse_rules(info)

    async def wait_next_close(self, stop: asyncio.Event) -> None:
        if self.speed_s > 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.speed_s)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(0)
        self.t += BAR_MS
        if self.t >= self.end:
            self.finished = True

    async def update(self, symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        d5, d1 = self.d5[symbol], self.d1[symbol]
        i = int(d5.open_time.searchsorted(self.t, side="right"))
        j = int(d1.open_time.searchsorted(self.t, side="right"))
        return d5.iloc[max(0, i - KEEP_5M):i].reset_index(drop=True), d1.iloc[max(0, j - KEEP_1H):j].reset_index(drop=True)

    async def price(self, symbol: str) -> float:
        d5 = self.d5[symbol]
        i = int(d5.open_time.searchsorted(self.t, side="right"))
        return float(d5.open.iloc[i]) if i < len(d5) else float(d5.close.iloc[-1])
