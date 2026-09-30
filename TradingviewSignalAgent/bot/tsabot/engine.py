"""Trading engine: one decision per closed 5m bar, one position per coin.

Mirrors the research backtest: signal on the close of bar t, entry at the next
open, TP/SL = entry +/- k*ATR(t) (stop checked first inside a bar), time exit at
the close of bar t+H.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import deque
from dataclasses import asdict, dataclass

import httpx

from .broker import Fill
from .config import Settings
from .exchange.binance import ExchangeError
from .risk import Risk
from .secrets import REDACT
from .store import Store
from .strategy import Signal, Strategy

log = logging.getLogger("tsabot.engine")
DAY_MS = 86_400_000
BAR_MS = 300_000


def transient(e: Exception) -> bool:
    """Network failures, timeouts, rate limits and exchange-side 5xx: retry, do not count as bot errors."""
    if isinstance(e, (httpx.TransportError, asyncio.TimeoutError, ConnectionError)):
        return True
    return isinstance(e, ExchangeError) and (e.status >= 500 or e.status in (408, 418, 429) or e.code == -1001)


@dataclass
class Position:
    symbol: str
    direction: int
    qty: float
    entry_price: float
    entry_time_ms: int
    signal_bar: int
    H: int
    tp: float | None
    sl: float | None
    emergency: float | None
    margin: float
    fee_in: float
    rule: int
    bars_held: int = 0
    last_bar: int = 0
    protected: bool = True       # False until the exchange confirms the closing stop order

    def stop_price(self) -> float | None:
        """The stop that is really used: the tighter of the ATR stop and the emergency stop."""
        xs = [x for x in (self.sl, self.emergency) if x is not None]
        return ((max if self.direction > 0 else min)(xs)) if xs else None


class Engine:
    def __init__(self, settings: Settings, strategy: Strategy, market, broker, store: Store):
        self.s = settings
        self.strategy = strategy
        self.market = market
        self.broker = broker
        self.store = store
        self.risk = Risk(settings)
        self.mode = settings.mode
        self.positions: dict[str, Position] = {
            k: Position(**v) for k, v in (store.get(f"positions:{self.mode}", {}) or {}).items()}
        self.rules = {}
        self.symbols: list[str] = []
        self.signals: dict[str, dict] = {}
        self.last_prices: dict[str, float] = {}
        self.feed = deque(maxlen=200)
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.last_bar_ms: int | None = None
        self.status_msg = "Durduruldu"
        self.errors = 0
        # symbol -> signal whose order outcome is unknown; persisted, so a restart cannot forget it
        self.quarantine: dict[str, Signal] = {
            k: Signal(**{**v, "features": {}}) for k, v in (store.get(f"pending:{self.mode}", {}) or {}).items()}
        self.verify_stops = True                  # check every exchange stop on the first bar after a start

    # ---------------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def emit(self, level: str, msg: str) -> None:
        msg = REDACT.clean(msg)
        ts = self._now()
        self.store.event(level, msg, ts)
        self.feed.appendleft({"ts": ts, "level": level, "msg": msg})
        getattr(log, "warning" if level == "warn" else "error" if level == "error" else "info")(msg)

    def _now(self) -> int:
        try:
            return int(self.market.now_ms())
        except Exception:
            return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)

    async def start(self) -> None:
        if self.running:
            return
        self.stop_event.clear()
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=90)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        self.status_msg = "Durduruldu"

    async def panic(self) -> None:
        """Stop the loop FIRST (no entry can start afterwards), then close every bot position."""
        self.stop_event.set()
        await self.stop()
        for sym in list(self.positions):
            try:
                px = await self.market.price(sym)
                await self._exit(sym, await self.broker.close(sym, self.positions[sym].direction,
                                                              self.positions[sym].qty, px), "Acil kapatma")
            except Exception as e:  # keep closing the others
                self.emit("error", f"{sym} kapatılamadı: {e}")
        self._persist()

    async def _run(self) -> None:
        try:
            self.status_msg = "Başlatılıyor"
            self.rules = await self.market.rules()
            self.symbols = [s for s in self.s.symbols if s in self.rules]
            missing = sorted(set(self.s.symbols) - set(self.symbols))
            if missing:
                self.emit("warn", f"Borsada işlem görmeyen / veri olmayan semboller atlandı: {missing}")
            self.verify_stops = True
            own = set(self.positions) | set(self.quarantine)
            for w in await self.broker.prepare(self.symbols, self.s.leverage, own=own):
                self.emit("warn", w)
            self.emit("info", f"Bot başladı ({self.mode}), {len(self.symbols)} USDT paritesi izleniyor, "
                              f"bütçe {self.s.budget_usdt:.2f} USDT, kaldıraç {self.s.leverage}x")
            self.status_msg = "Çalışıyor"
            while not self.stop_event.is_set():
                await self.market.wait_next_close(self.stop_event)
                if self.stop_event.is_set():
                    break
                try:
                    await self.on_bar()
                    self.errors = 0
                except Exception as e:
                    if transient(e):
                        # internet / Binance outage: keep running (exchange stops protect open positions)
                        self.emit("warn", f"Bağlantı sorunu, sonraki mumda tekrar denenecek: {type(e).__name__}: {e}")
                        continue
                    self.errors += 1
                    self.emit("error", f"Döngü hatası: {type(e).__name__}: {e}")
                    if self.errors >= 5:
                        self.emit("error", "Art arda 5 hata, bot durduruldu.")
                        break
                if getattr(self.market, "finished", False):
                    self.emit("info", "Replay verisi bitti.")
                    break
        except Exception as e:
            self.emit("error", f"Başlatma hatası: {type(e).__name__}: {e}")
        finally:
            self.status_msg = "Durduruldu"

    # ---------------------------------------------------------------- per bar
    def realized(self) -> tuple[float, float]:
        now = self._now()
        return self.store.realized(self.mode), self.store.realized(self.mode, now - now % DAY_MS)

    async def on_bar(self) -> None:
        data = {}
        failed = {}
        sem = asyncio.Semaphore(8)

        async def upd(sym):
            async with sem:
                try:
                    data[sym] = await self.market.update(sym)
                except Exception as e:          # one bad symbol must not stop the others
                    failed[sym] = e
        await asyncio.gather(*(upd(s) for s in self.symbols))
        if failed:
            if not data:
                raise next(iter(failed.values()))
            e = next(iter(failed.values()))
            self.emit("warn", f"{len(failed)} sembolün verisi alınamadı ({', '.join(sorted(failed)[:5])}): "
                              f"{type(e).__name__}: {e}")
        for sym, (k5, _) in data.items():
            if len(k5):
                self.last_prices[sym] = float(k5["close"].iloc[-1])
                self.last_bar_ms = int(k5["open_time"].iloc[-1])

        await self._reconcile()
        await self._protect()

        # 1) evaluate the closed bar for every coin (entry AND exit rules) -----------------------------------------------------------
        loop = asyncio.get_running_loop()
        new = []
        now = self._now()
        for sym in self.symbols:
            k5, k1 = data.get(sym, (None, None))
            if k5 is None or not len(k5) or sym in getattr(self.broker, "external", ()) or sym in self.quarantine:
                continue
            if now - int(k5["open_time"].iloc[-1]) > 3 * 300_000:   # stale candles -> never trade on them
                self.emit("warn", f"{sym}: son mum eski ({(now - int(k5['open_time'].iloc[-1])) // 60000} dk), atlandı")
                continue
            sig = await loop.run_in_executor(None, self.strategy.evaluate, sym, k5, k1, self.s.allow_long,
                                             self.s.allow_short)
            if sig is None:
                continue
            self.signals[sym] = {"symbol": sym, "bar_time": sig.bar_time, "direction": sig.direction,
                                 "rule": sig.rule, "close": sig.close, "atr": sig.atr, "features": sig.features}
            if sig.direction != 0:
                self.emit("signal", f"{sym} {'AL (long)' if sig.direction > 0 else 'SAT (short)'} sinyali, "
                                    f"kural #{sig.rule + 1}, kapanış {sig.close:g}")
                new.append(sig)

        # 2) exits: exchange/paper TP-SL, indicator exit (ÇIK), time -------------------------------------------------------------
        for sym, fill in (await self.broker.closed_by_exchange(dict(self.positions))).items():
            await self._exit(sym, fill, "TP/SL (borsa)")
        for sym, pos in list(self.positions.items()):
            k5 = data.get(sym, (None,))[0]
            if k5 is None or not len(k5):
                continue
            bar = k5.iloc[-1]
            bt = int(bar["open_time"])
            if bt <= max(pos.signal_bar, pos.last_bar):
                continue
            pos.bars_held = int((bt - pos.signal_bar) // BAR_MS)   # counts missed bars / bot downtime too
            pos.last_bar = bt
            if self.broker.simulated:
                hit = self._bracket_hit(pos, bar)
                if hit:
                    px, why = hit
                    await self._exit(sym, await self.broker.close(sym, pos.direction, pos.qty, px), why)
                    continue
            ev = self.signals.get(sym)
            dm = self.strategy.params(pos.direction)
            if ev and ev["bar_time"] == bt and dm.should_exit(ev["features"]):
                await self._exit(sym, await self.broker.close(sym, pos.direction, pos.qty, float(bar["close"])),
                                 "ÇIK sinyali (kâr al)")
                continue
            if pos.bars_held >= pos.H:
                await self._exit(sym, await self.broker.close(sym, pos.direction, pos.qty, float(bar["close"])),
                                 f"Süre doldu ({pos.H} mum)")

        # 3) entries -----------------------------------------------------------
        for sig in new:
            if sig.symbol not in self.positions:
                await self._enter(sig)
        self._persist()
        tot, _ = self.realized()
        unreal = sum(p.direction * (self.last_prices.get(s, p.entry_price) - p.entry_price) * p.qty
                     for s, p in self.positions.items())
        self.store.add_equity(self._now(), self.mode, self.s.budget_usdt + tot + unreal, tot)
        reason = self.risk.stop_reason(tot)
        if reason:
            self.emit("error", reason)
            self.stop_event.set()

    @staticmethod
    def _bracket_hit(pos: Position, bar) -> tuple[float, str] | None:
        """Same convention as tsa.labels.trade_outcomes: stop first, gaps fill at the open."""
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        first = pos.bars_held == 1
        stops = [x for x in (pos.sl, pos.emergency) if x]
        if pos.direction > 0:
            sl = max(stops) if stops else None
            if sl is not None and l <= sl:
                return (sl if first else min(sl, o)), "Zarar kes (SL)"
            if pos.tp and h >= pos.tp:
                return (pos.tp if first else max(pos.tp, o)), "Kâr al (TP)"
        else:
            sl = min(stops) if stops else None
            if sl is not None and h >= sl:
                return (sl if first else max(sl, o)), "Zarar kes (SL)"
            if pos.tp and l <= pos.tp:
                return (pos.tp if first else min(pos.tp, o)), "Kâr al (TP)"
        return None

    async def _reconcile(self) -> None:
        """Symbols whose order result was lost: adopt the position if the exchange has it,
        release the symbol if it is flat, keep it blocked if the exchange cannot be asked."""
        for sym, sig in list(self.quarantine.items()):
            try:
                amt, entry = await self.broker.position_of(sym)
            except Exception as e:
                self.emit("error", f"{sym}: pozisyon durumu sorgulanamadı ({e}); sembol kilitli kalıyor")
                continue
            del self.quarantine[sym]
            self._persist_pending()
            if amt == 0.0:
                self.emit("info", f"{sym}: borsada pozisyon yok, sembol serbest")
                continue
            d = 1 if amt > 0 else -1
            dm = self.strategy.params(d)
            entry = entry or sig.close
            tp = entry + d * dm.tp_atr * sig.atr if dm.tp_atr else None
            sl = entry - d * dm.sl_atr * sig.atr if dm.sl_atr else None
            emergency = entry * (1 - d * self.s.emergency_stop_pct / 100)
            self.positions[sym] = Position(sym, d, abs(amt), entry, self._now(), sig.bar_time, dm.H, tp, sl, emergency,
                                           abs(amt) * entry / self.s.leverage, abs(amt) * entry * 0.0005, sig.rule,
                                           protected=False)
            self._persist()
            if tp:
                try:
                    await self.broker.set_brackets(sym, d, tp, None, self.rules[sym])
                except Exception as e:
                    self.emit("error", f"{sym} kâr al emri verilemedi ({e})")
            self.emit("warn", f"{sym}: yanıtı kaybolan emir borsada dolmuş, pozisyon sahiplenildi ({amt:g} @ {entry:g})")

    async def _protect(self) -> None:
        """Every bot position must have its closing stop on the exchange. Missing stops are placed again
        each bar; if the price is already beyond the stop the position is closed at once."""
        if self.broker.simulated:
            return
        all_ok = True
        for sym, pos in list(self.positions.items()):
            if pos.protected and not self.verify_stops:
                continue
            try:
                ok = await self.broker.ensure_stop(sym, pos.direction, pos.stop_price(), self.rules[sym])
                if ok:
                    if not pos.protected:
                        self.emit("info", f"{sym}: stop emri borsada kuruldu ({pos.stop_price():.6g})")
                    pos.protected = True
                else:
                    px = await self.market.price(sym)
                    await self._exit(sym, await self.broker.close(sym, pos.direction, pos.qty, px),
                                     "Stop seviyesi geçilmişti (koruma)")
            except Exception as e:
                all_ok = False
                pos.protected = False
                self.emit("error", f"{sym}: stop emri doğrulanamadı / kurulamadı ({type(e).__name__}: {e}); "
                                   "sonraki mumda tekrar denenecek")
        if all_ok:
            self.verify_stops = False
        self._persist()

    def _persist_pending(self) -> None:
        self.store.put(f"pending:{self.mode}", {k: {**asdict(v), "features": {}} for k, v in self.quarantine.items()})

    async def _enter(self, sig) -> None:
        if self.stop_event.is_set():
            return
        tot, today = self.realized()
        avail = await self.broker.available_usdt()
        ok, why = self.risk.can_open([p.margin for p in self.positions.values()], tot, today, avail)
        if not ok:
            self.emit("info", f"{sig.symbol} sinyali atlandı: {why}")
            return
        rules = self.rules[sig.symbol]
        px = await self.market.price(sig.symbol)
        if self.stop_event.is_set():        # stop / panic arrived while waiting for the price
            return
        qty, margin = self.risk.size(px, rules, tot)
        if qty <= 0:
            self.emit("warn", f"{sig.symbol}: pozisyon başına bütçe borsanın minimum emir tutarının altında")
            return
        dm = self.strategy.params(sig.direction)
        amt, _ = await self.broker.position_of(sig.symbol)
        if amt != 0.0:                       # opened by hand after the bot started: never merge into it
            getattr(self.broker, "external", set()).add(sig.symbol)
            self.emit("warn", f"{sig.symbol}: borsada bu coinde botun açmadığı bir pozisyon var, sinyal atlandı")
            return
        if self.stop_event.is_set():
            return
        # persisted before the order is sent: if the answer (or the whole program) is lost, the next
        # bar / next start asks the exchange instead of forgetting or duplicating the order
        self.quarantine[sig.symbol] = sig
        self._persist_pending()
        try:
            fill = await self.broker.open(sig.symbol, sig.direction, qty, px, rules)
        except Exception as e:
            self.emit("error", f"{sig.symbol} emir hatası: {type(e).__name__}: {e}; borsa ile mutabakat bekleniyor")
            return
        d = sig.direction
        tp = fill.price + d * dm.tp_atr * sig.atr if dm.tp_atr else None
        sl = fill.price - d * dm.sl_atr * sig.atr if dm.sl_atr else None
        emergency = fill.price * (1 - d * self.s.emergency_stop_pct / 100)
        stop = (max if d > 0 else min)(x for x in (sl, emergency) if x is not None)
        pos = Position(sig.symbol, d, fill.qty, fill.price, self._now(), sig.bar_time, dm.H, tp, sl, emergency,
                       fill.qty * fill.price / self.s.leverage, fill.fee, sig.rule, protected=False)
        self.positions[sig.symbol] = pos
        self._persist()                      # recorded before anything else can fail
        self.quarantine.pop(sig.symbol, None)
        self._persist_pending()
        try:
            await self.broker.set_brackets(sig.symbol, d, tp, stop, rules)
            pos.protected = True
            self._persist()
        except Exception as e:
            self.emit("error", f"{sig.symbol} TP/SL emri verilemedi ({e}); pozisyon kapatılıyor")
            try:
                await self._exit(sig.symbol, await self.broker.close(sig.symbol, d, fill.qty, fill.price),
                                 "Koruma hatası")
            except Exception as e2:          # stays unprotected -> _protect retries every bar
                self.emit("error", f"{sig.symbol} kapatılamadı ({type(e2).__name__}: {e2}); stop her mumda "
                                   "yeniden denenecek")
            return
        self.emit("trade", f"{sig.symbol} {'LONG' if d > 0 else 'SHORT'} açıldı: {fill.qty:g} @ {fill.price:g}, "
                           f"marjin {pos.margin:.2f} USDT" + (f", TP {tp:.6g}" if tp else "")
                  + f", stop {stop:.6g}" + (" + ÇIK sinyali" if dm.exit_rules else "") + f", en geç {dm.H} mum")

    async def _exit(self, sym: str, fill: Fill, reason: str) -> None:
        pos = self.positions.pop(sym, None)
        if pos is None:
            return
        fees = pos.fee_in + fill.fee
        pnl = pos.direction * (fill.price - pos.entry_price) * pos.qty - fees
        self.store.add_trade({"mode": self.mode, "symbol": sym, "direction": pos.direction, "qty": pos.qty,
                              "entry_time": pos.entry_time_ms, "entry_price": pos.entry_price,
                              "exit_time": self._now(), "exit_price": fill.price, "reason": reason, "fees": fees,
                              "pnl": pnl, "margin": pos.margin, "rule": pos.rule})
        self.emit("trade", f"{sym} kapandı ({reason}) @ {fill.price:g}, net {pnl:+.2f} USDT (komisyon {fees:.2f})")
        self._persist()

    def _persist(self) -> None:
        self.store.put(f"positions:{self.mode}", {k: asdict(v) for k, v in self.positions.items()})

    # ---------------------------------------------------------------- views
    def snapshot(self) -> dict:
        tot, today = self.realized()
        unreal = {s: p.direction * (self.last_prices.get(s, p.entry_price) - p.entry_price) * p.qty
                  for s, p in self.positions.items()}
        used = sum(p.margin for p in self.positions.values())
        return {"running": self.running, "status": self.status_msg, "mode": self.mode,
                "budget": self.s.budget_usdt, "effective_budget": self.risk.effective_budget(tot),
                "margin_used": used, "realized": tot, "realized_today": today, "unrealized": sum(unreal.values()),
                "positions": [{**asdict(p), "unrealized": unreal[s], "last_price": self.last_prices.get(s)}
                              for s, p in self.positions.items()],
                "last_bar": self.last_bar_ms, "symbols": len(self.symbols) or len(self.s.symbols),
                "stats": self.store.stats(self.mode)}
