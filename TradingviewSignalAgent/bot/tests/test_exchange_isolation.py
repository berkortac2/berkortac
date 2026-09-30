"""Binance client and broker (v1.1): request-weight limiter, safe retries, bot-tagged orders that never touch
the user's own orders or contracts, quantity-based stops, reconciliation with manual changes."""
import asyncio
from decimal import Decimal

import httpx
import pytest

import tsabot  # noqa: F401
from tsabot.broker import BinanceBroker
from tsabot.engine import Position
from tsabot.exchange.binance import (ALLOWED, BOT_PREFIX, BinanceFutures, ExchangeError, RateLimiter, SymbolRules,
                                     bot_id, is_bot_order, weight_of)
from tsabot.secrets import Secret

from test_security import KEY, SECRET  # noqa: E402

RULES = SymbolRules("AAAUSDT", Decimal("0.001"), Decimal("0.001"), Decimal("1000"), Decimal("0.01"), 5.0)


def client(handler):
    c = BinanceFutures("testnet", Secret(KEY), Secret(SECRET), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c.synced_at = 1e18                  # no clock sync requests in these tests
    c.RETRY_DELAYS = (0.0, 0.0)
    return c


# ---------------------------------------------------------------- rate limiter
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


def test_weights_follow_binance_docs():
    assert weight_of("GET", "/fapi/v1/klines", {"limit": 99}) == 1
    assert weight_of("GET", "/fapi/v1/klines", {"limit": 300}) == 2
    assert weight_of("GET", "/fapi/v1/klines", {"limit": 1000}) == 5
    assert weight_of("GET", "/fapi/v1/klines", {"limit": 1500}) == 10
    assert weight_of("GET", "/fapi/v2/ticker/price", {}) == 2 and weight_of("GET", "/fapi/v2/ticker/price", {"symbol": "X"}) == 1
    assert weight_of("GET", "/fapi/v1/positionSide/dual", {}) == 30
    assert weight_of("POST", "/fapi/v1/order", {}) == 0


def test_limiter_waits_before_the_budget_is_exceeded():
    ck = Clock()
    lim = RateLimiter(per_min=100, critical_per_min=150, clock=ck, sleep=ck.sleep)

    async def go():
        for _ in range(20):
            await lim.acquire(5)                 # 100 weight: exactly the budget, no waiting
        t0 = ck.t
        await lim.acquire(5)                     # the 101st point must wait for the window to move
        return ck.t - t0
    waited = asyncio.run(go())
    assert waited >= 59.0 and lim.used() <= 100


def test_critical_orders_use_the_reserve_and_server_count_is_respected():
    ck = Clock()
    lim = RateLimiter(per_min=100, critical_per_min=150, clock=ck, sleep=ck.sleep)

    async def go():
        for _ in range(20):
            await lim.acquire(5)
        t0 = ck.t
        await lim.acquire(5, critical=True)      # closing / stop orders still go out at once
        return ck.t - t0
    assert asyncio.run(go()) == 0
    lim2 = RateLimiter(per_min=100, clock=ck, sleep=ck.sleep)
    lim2.observe({"x-mbx-used-weight-1m": "99"})  # another program on this IP used the minute
    assert lim2.used() == 99
    asyncio.run(lim2.acquire(5))
    assert ck.t > 1000.0 + 0.04                  # had to wait for the server's minute to end


def test_order_count_limit():
    ck = Clock()
    lim = RateLimiter(orders_per_10s=3, clock=ck, sleep=ck.sleep)

    async def go():
        for _ in range(4):
            await lim.acquire(0, order=True)
    asyncio.run(go())
    assert ck.t >= 1010.0


def test_used_weight_header_is_read_from_every_response():
    def handler(req):
        return httpx.Response(200, json={"serverTime": 1}, headers={"X-MBX-USED-WEIGHT-1M": "1234"})
    c = client(handler)
    asyncio.run(c.request("GET", "/fapi/v1/time"))
    assert c.limiter.server_used == 1234


# ---------------------------------------------------------------- retries
def test_get_is_retried_after_network_errors_and_5xx():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("dns")
        if len(calls) == 2:
            return httpx.Response(503, json={"code": -1001, "msg": "Internal error"})
        return httpx.Response(200, json={"price": "5"})
    c = client(handler)
    assert asyncio.run(c.price("BTCUSDT")) == 5.0 and len(calls) == 3


def test_orders_are_never_sent_twice_when_the_answer_is_lost():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        raise httpx.ReadTimeout("no answer")          # may have been executed on the exchange
    c = client(handler)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(c.market_order("BTCUSDT", "BUY", 0.01))
    assert calls == ["/fapi/v1/order"]


def test_order_503_is_not_repeated_but_unsent_order_is():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(503, json={"code": -1001, "msg": "Unknown error"})
    c = client(handler)
    with pytest.raises(ExchangeError):
        asyncio.run(c.market_order("BTCUSDT", "BUY", 0.01))
    assert len(calls) == 1
    calls2 = []

    def handler2(req):
        calls2.append(1)
        if len(calls2) == 1:
            raise httpx.ConnectError("connection refused")   # never reached Binance: safe to send again
        return httpx.Response(200, json={"orderId": 7, "status": "FILLED", "executedQty": "0.01", "avgPrice": "1"})
    assert asyncio.run(client(handler2).market_order("BTCUSDT", "BUY", 0.01))["orderId"] == 7 and len(calls2) == 2


def test_consecutive_network_errors_are_counted_for_the_ui():
    def handler(req):
        raise httpx.ConnectError("down")
    c = client(handler)
    with pytest.raises(httpx.ConnectError):
        asyncio.run(c.price("BTCUSDT"))
    assert c.net_errors == 3


# ---------------------------------------------------------------- the user's own orders are untouchable
def test_cancel_all_orders_endpoint_is_not_reachable():
    assert ("DELETE", "/fapi/v1/algoOpenOrders") not in ALLOWED
    assert ("DELETE", "/fapi/v1/allOpenOrders") not in ALLOWED


def test_bot_order_ids():
    i = bot_id("S")
    assert i.startswith(BOT_PREFIX) and len(i) <= 36 and i.replace("-", "").isalnum()
    assert is_bot_order({"clientAlgoId": i}) and not is_bot_order({"clientAlgoId": "web_abc123"})
    assert not is_bot_order({})


def test_stop_is_reduce_only_for_the_bots_quantity():
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, json={"algoId": 1})
    c = client(handler)
    asyncio.run(c.conditional_close("AAAUSDT", "SELL", "STOP_MARKET", 9.5, 1.25))
    q = dict(seen[0].params)
    assert q["quantity"] == "1.25" and q["reduceOnly"] == "true" and "closePosition" not in q
    assert q["clientAlgoId"].startswith(BOT_PREFIX) and q["priceProtect"] == "false"


def test_only_bot_conditionals_are_cancelled():
    deleted = []
    orders = [{"algoId": 1, "clientAlgoId": "tsaS17000000000000001", "orderType": "STOP_MARKET", "side": "SELL"},
              {"algoId": 2, "clientAlgoId": "ios_mystop", "orderType": "STOP_MARKET", "side": "SELL"},
              {"algoId": 3, "clientAlgoId": "tsaT17000000000000002", "orderType": "TAKE_PROFIT_MARKET", "side": "SELL"}]

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=orders)
        assert req.method == "DELETE" and req.url.path == "/fapi/v1/algoOrder"
        deleted.append(int(req.url.params["algoId"]))
        return httpx.Response(200, json={"code": "200"})
    n = asyncio.run(client(handler).cancel_conditionals("AAAUSDT"))
    assert n == 2 and sorted(deleted) == [1, 3]         # the user's stop (algoId 2) stays


class FC:
    """Fake client for the broker."""

    def __init__(self, open_orders=(), amt=0.0, fills=None):
        self.orders, self.amt, self.fills = list(open_orders), amt, list(fills or [])
        self.placed, self.cancelled, self.market = [], [], []

    async def open_conditionals(self, symbol):
        return self.orders

    async def cancel_algo(self, symbol, algo_id):
        self.cancelled.append(algo_id)

    async def conditional_close(self, symbol, side, kind, trigger, qty=None):
        self.placed.append((kind, trigger, qty))
        return {"algoId": 99}

    async def cancel_conditionals(self, symbol, only=None):
        return 0

    async def market_order(self, symbol, side, qty, reduce_only=False, client_id=None):
        self.market.append((side, qty, reduce_only, client_id))
        f = self.fills.pop(0) if self.fills else {"status": "FILLED", "executedQty": str(qty), "avgPrice": "10"}
        return {"orderId": 1, **f}

    async def positions(self, symbol=None):
        return [{"symbol": "AAAUSDT", "positionAmt": str(self.amt), "entryPrice": "10"}]


def test_ensure_stop_keeps_a_correct_bot_stop_and_replaces_a_wrong_one():
    ok = FC([{"algoId": 5, "clientAlgoId": "tsaS1", "orderType": "STOP_MARKET", "side": "SELL", "quantity": "1.000"}])
    assert asyncio.run(BinanceBroker(ok).ensure_stop("AAAUSDT", 1, 9.0, RULES, qty=1.0)) and not ok.placed
    wrong = FC([{"algoId": 6, "clientAlgoId": "tsaS1", "orderType": "STOP_MARKET", "side": "SELL", "quantity": "2.000"},
                {"algoId": 7, "clientAlgoId": "manual", "orderType": "STOP_MARKET", "side": "SELL", "quantity": "1.000"}])
    assert asyncio.run(BinanceBroker(wrong).ensure_stop("AAAUSDT", 1, 9.0, RULES, qty=1.0))
    assert wrong.cancelled == [6] and wrong.placed == [("STOP_MARKET", 9.0, 1.0)]   # the user's order 7 untouched


def test_partial_close_never_closes_contracts_the_user_added():
    # exchange holds 3.0 (bot 1.0 + user 2.0); the bot's market close fills only 0.6
    c = FC(amt=3.0, fills=[{"status": "EXPIRED", "executedQty": "0.6", "avgPrice": "10"}])
    f = asyncio.run(BinanceBroker(c).close("AAAUSDT", 1, 1.0, 10.0))
    assert [m[1] for m in c.market] == [1.0, pytest.approx(0.4)] and all(m[2] for m in c.market)
    assert f.qty == pytest.approx(1.0)
    assert all(m[3].startswith(BOT_PREFIX) for m in c.market)


def test_quantities_follow_manual_changes():
    pos = Position("AAAUSDT", 1, 2.0, 10.0, 0, 0, 96, None, 9.0, 9.2, 20.0, 0.01, 0)
    b = BinanceBroker(FC(amt=1.5))
    assert asyncio.run(b.sync_quantities({"AAAUSDT": pos})) == {"AAAUSDT": 1.5}      # closed partly by hand
    b2 = BinanceBroker(FC(amt=5.0))
    assert asyncio.run(b2.sync_quantities({"AAAUSDT": pos})) == {} and "AAAUSDT" in b2.warned_extra
    b3 = BinanceBroker(FC(amt=-1.0))                                                  # flipped to short by hand
    assert asyncio.run(b3.sync_quantities({"AAAUSDT": pos})) == {}


def test_manually_skipped_coin_is_released_when_flat():
    b = BinanceBroker(FC(amt=0.0))
    b.external = {"AAAUSDT"}
    assert asyncio.run(b.release_external()) == ["AAAUSDT"] and not b.external
    b2 = BinanceBroker(FC(amt=1.0))
    b2.external = {"AAAUSDT"}
    assert asyncio.run(b2.release_external()) == [] and b2.external == {"AAAUSDT"}


def test_leverage_change_is_applied_before_the_next_position():
    b = BinanceBroker(FC())
    b.prepared = {"AAAUSDT"}
    b.set_leverage(1)
    assert b.prepared == {"AAAUSDT"}
    b.set_leverage(3)
    assert b.leverage == 3 and not b.prepared


def test_account_view_separates_nothing_but_reports_all_positions():
    class C(FC):
        async def account(self):
            return {"availableBalance": "700", "totalWalletBalance": "1000", "totalUnrealizedProfit": "3",
                    "assets": [{"asset": "USDT", "walletBalance": "1000", "marginBalance": "1003"}]}

        async def positions(self, symbol=None):
            return [{"symbol": "AAAUSDT", "positionAmt": "1", "entryPrice": "10", "markPrice": "11",
                     "unRealizedProfit": "1", "isolatedMargin": "10"},
                    {"symbol": "BBBUSDT", "positionAmt": "0"}]
    v = asyncio.run(BinanceBroker(C()).account_view())
    assert v["wallet"] == 1000 and v["available"] == 700 and len(v["positions"]) == 1
    assert v["positions"][0]["margin"] == 10
