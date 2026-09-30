"""Robustness fixes from the second full review: order sequencing, partial / unfilled market orders,
fees paid in BNB, holding time after downtime, bad symbols, network outages, manual positions,
missing indicator values, kline gaps, clock drift."""
import asyncio
from decimal import Decimal

import httpx
import numpy as np
import pandas as pd
import pytest

import tsabot  # noqa: F401
from tsabot.broker import BinanceBroker, Fill
from tsabot.config import Settings
from tsabot.engine import Engine, Position, transient
from tsabot.exchange.binance import BinanceFutures, ExchangeError, SymbolRules, num
from tsabot.market import BAR_MS, LiveMarket
from tsabot.secrets import Secret
from tsabot.store import Store
from tsabot.strategy import DirModel, Signal

from test_security import KEY, SECRET  # noqa: E402

RULES = SymbolRules("AAAUSDT", Decimal("0.001"), Decimal("0.001"), Decimal("1000"), Decimal("0.01"), 5.0)


def test_num_is_exact_decimal():
    assert num(1e-05) == "0.00001" and num(100.0) == "100" and num(0.1 + 0.2) == "0.30000000000000004"
    assert num(0.00000012345) == "0.00000012345" and num(12.5) == "12.5"


class FakeClient:
    """Records the order of calls; market orders fill according to `fills`."""

    def __init__(self, fills=None, fail_market=False, amt=0.0, trades=None):
        self.calls, self.fills, self.fail, self.amt, self.trades = [], list(fills or []), fail_market, amt, trades

    async def market_order(self, symbol, side, qty, reduce_only=False, client_id=None):
        self.calls.append(("market", side, qty, reduce_only))
        if self.fail:
            raise ExchangeError(400, -2022, "ReduceOnly Order is rejected")
        f = self.fills.pop(0) if self.fills else {"status": "FILLED", "executedQty": str(qty), "avgPrice": "10"}
        return {"orderId": 1, **f}

    async def order(self, symbol, oid):
        self.calls.append(("query",))
        return {"orderId": oid, "status": "FILLED", "executedQty": "1", "avgPrice": "10"}

    async def cancel_conditionals(self, symbol):
        self.calls.append(("cancel",))

    async def positions(self, symbol=None):
        return [{"symbol": "AAAUSDT", "positionAmt": str(self.amt), "entryPrice": "10"}]

    async def set_isolated(self, s):
        pass

    async def set_leverage(self, s, lev):
        pass

    async def user_trades(self, sym, start):
        return self.trades or []

    async def price(self, sym):
        return 10.0


def broker(client):
    b = BinanceBroker(client)
    b.leverage = 1
    return b


def test_close_sends_market_order_before_removing_the_stop():
    c = FakeClient()
    asyncio.run(broker(c).close("AAAUSDT", 1, 1.0, 10.0))
    assert [x[0] for x in c.calls] == ["market", "cancel"]


def test_failed_close_keeps_the_exchange_stop():
    c = FakeClient(fail_market=True)
    with pytest.raises(ExchangeError):
        asyncio.run(broker(c).close("AAAUSDT", 1, 1.0, 10.0))
    assert ("cancel",) not in c.calls


def test_open_removes_stale_stops_first():
    c = FakeClient()
    asyncio.run(broker(c).open("AAAUSDT", 1, 1.0, 10.0, RULES))
    assert [x[0] for x in c.calls] == ["cancel", "market"]


def test_unfilled_market_order_raises_and_partial_close_is_completed():
    c = FakeClient(fills=[{"status": "EXPIRED", "executedQty": "0", "avgPrice": "0"}])
    with pytest.raises(ExchangeError):
        asyncio.run(broker(c).open("AAAUSDT", 1, 1.0, 10.0, RULES))
    c = FakeClient(fills=[{"status": "EXPIRED", "executedQty": "0.6", "avgPrice": "10"},
                          {"status": "FILLED", "executedQty": "0.4", "avgPrice": "11"}], amt=0.4)
    f = asyncio.run(broker(c).close("AAAUSDT", 1, 1.0, 10.0))
    assert abs(f.qty - 1.0) < 1e-12 and abs(f.price - 10.4) < 1e-9
    assert [x[0] for x in c.calls] == ["market", "market", "cancel"] and c.calls[1][2] == 0.4


def test_pending_market_order_is_queried_until_final():
    c = FakeClient(fills=[{"status": "NEW", "executedQty": "0", "avgPrice": "0"}])
    f = asyncio.run(broker(c).open("AAAUSDT", 1, 1.0, 10.0, RULES))
    assert ("query",) in c.calls and f.qty == 1.0


def test_bnb_commission_is_not_added_as_usdt():
    trades = [{"side": "SELL", "qty": "1", "price": "10", "commission": "0.00001", "commissionAsset": "BNB"},
              {"side": "SELL", "qty": "1", "price": "10", "commission": "0.005", "commissionAsset": "USDT"}]
    c = FakeClient(amt=0.0, trades=trades)
    pos = Position("AAAUSDT", 1, 2.0, 9.0, 0, 0, 6, None, 8.0, 8.0, 18.0, 0.0, 0)
    out = asyncio.run(broker(c).closed_by_exchange({"AAAUSDT": pos}))
    assert abs(out["AAAUSDT"].fee - (10 * 1 * 0.0005 + 0.005)) < 1e-12


# ---------------------------------------------------------------- engine
class Mkt:
    def __init__(self, bars, fail=()):
        self.bars, self.fail, self.t = bars, set(fail), bars[-1]

    def now_ms(self):
        return self.t + BAR_MS

    async def update(self, sym):
        if sym in self.fail:
            raise ExchangeError(400, -1121, "Invalid symbol")
        rows = [{"open_time": t, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0,
                 "close_time": t + BAR_MS - 1} for t in self.bars]
        return pd.DataFrame(rows), pd.DataFrame(rows)

    async def price(self, sym):
        return 10.0


class Strat:
    def __init__(self, direction=0):
        self.d = direction

    def evaluate(self, sym, k5, k1, al, ash):
        return Signal(sym, int(k5["open_time"].iloc[-1]), self.d, 0, 10.0, 0.5, {})

    def params(self, d):
        return DirModel(rules=[], H=6, tp_atr=None, sl_atr=2.0)


class Brk:
    simulated = False

    def __init__(self, amt=0.0):
        self.external, self.amt, self.closed, self.opened = set(), amt, [], []

    async def closed_by_exchange(self, o):
        return {}

    async def close(self, sym, d, qty, px, slip=True):
        self.closed.append(sym)
        return Fill(px, qty, 0.0)

    async def position_of(self, sym):
        return self.amt, 10.0

    async def available_usdt(self):
        return None

    async def open(self, *a):
        self.opened.append(a[0])
        return Fill(10.0, a[2], 0.0)

    async def set_brackets(self, *a):
        pass


def eng(market, broker, strat, symbols=("AAAUSDT",)):
    e = Engine(Settings(symbols=list(symbols), mode="paper"), strat, market, broker, Store(":memory:"))
    e.rules = {s: RULES for s in symbols}
    e.symbols = list(symbols)
    return e


def test_holding_time_counts_bars_missed_while_offline():
    t0 = 1_700_000_000_000 - (1_700_000_000_000 % BAR_MS)
    b = Brk()
    e = eng(Mkt([t0 + 10 * BAR_MS]), b, Strat())       # the bot was off for 10 bars
    e.positions["AAAUSDT"] = Position("AAAUSDT", 1, 1.0, 10.0, t0, t0, 6, None, 9.0, 9.2, 10.0, 0.0, 0,
                                      bars_held=2, last_bar=t0 + 2 * BAR_MS)
    asyncio.run(e.on_bar())
    assert b.closed == ["AAAUSDT"] and not e.positions      # H=6 already passed -> time exit now


def test_one_bad_symbol_does_not_block_the_others():
    t = 1_700_000_100_000 - (1_700_000_100_000 % BAR_MS)
    b = Brk()
    e = eng(Mkt([t], fail={"BBBUSDT"}), b, Strat(1), symbols=("AAAUSDT", "BBBUSDT"))
    asyncio.run(e.on_bar())
    assert b.opened == ["AAAUSDT"]
    e2 = eng(Mkt([t], fail={"AAAUSDT"}), Brk(), Strat(1))
    with pytest.raises(ExchangeError):                  # nothing at all could be read -> a real error
        asyncio.run(e2.on_bar())


def test_manual_position_in_symbol_is_never_merged():
    t = 1_700_000_100_000 - (1_700_000_100_000 % BAR_MS)
    b = Brk(amt=3.0)
    e = eng(Mkt([t]), b, Strat(1))
    asyncio.run(e.on_bar())
    assert not b.opened and "AAAUSDT" in b.external and not e.positions


def test_network_errors_are_transient_bugs_are_not():
    assert transient(httpx.ConnectError("down")) and transient(httpx.ReadTimeout("slow"))
    assert transient(ExchangeError(503, None, "busy")) and transient(ExchangeError(429, -1003, "too many"))
    assert not transient(ExchangeError(400, -1121, "Invalid symbol")) and not transient(KeyError("x"))


def test_missing_feature_never_matches_a_rule():
    d = DirModel(rules=[[("x", "<=", 0.5)]], exit_rules=[[("wt", "<=", 0.5)]])
    assert d.match({"x": np.nan}) == -1 and d.match({}) == -1 and d.match({"x": 0.1}) == 0
    assert not d.should_exit({"wt": None}) and d.should_exit({"wt": 0.2})


# ---------------------------------------------------------------- market / client
def test_kline_request_covers_the_whole_gap():
    m = LiveMarket(BinanceFutures("live"))
    now = 1_700_000_000_000
    assert m._need("X", now) == (1000, 300)
    bars = pd.DataFrame({"open_time": [now - 40 * BAR_MS]})
    m.k5["X"], m.k1h["X"] = bars, pd.DataFrame({"open_time": [now - 3 * 3_600_000]})
    n5, n1 = m._need("X", now)
    assert n5 >= 41 and n1 >= 4
    m.k5["X"] = pd.DataFrame({"open_time": [now - 2000 * BAR_MS]})
    assert m._need("X", now) == (1000, 300) and "X" not in m.k5


def test_timestamp_error_resyncs_and_retries_once():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        if req.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if seen.count("/fapi/v3/account") == 1:
            return httpx.Response(400, json={"code": -1021, "msg": "Timestamp outside recvWindow"})
        return httpx.Response(200, json={"availableBalance": "5"})
    c = BinanceFutures("testnet", Secret(KEY), Secret(SECRET), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(c.account())["availableBalance"] == "5"
    assert seen.count("/fapi/v3/account") == 2 and seen.count("/fapi/v1/time") == 2
