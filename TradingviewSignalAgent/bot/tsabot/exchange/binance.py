"""Minimal Binance USDT-M futures REST client with a hard endpoint allow-list.

Nothing outside ALLOWED can be called: there is no code path to wallet endpoints
(withdraw, transfer, deposit, convert, ...). The only non-futures call is the
read-only GET /sapi/v1/account/apiRestrictions, used to REFUSE live trading when
the key has withdrawal or transfer permissions.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from urllib.parse import urlencode, urlsplit

import httpx
import pandas as pd

from ..secrets import REDACT, Secret

log = logging.getLogger("tsabot.binance")

TESTNET_BASES = ("https://demo-fapi.binance.com", "https://testnet.binancefuture.com")
# Binance "Demo Trading" (demo-fapi) is the documented futures test environment; the older
# testnet.binancefuture.com keys can be used with TSABOT_TESTNET_BASE=https://testnet.binancefuture.com
_tb = os.environ.get("TSABOT_TESTNET_BASE", TESTNET_BASES[0])
BASES = {"live": "https://fapi.binance.com", "testnet": _tb if _tb in TESTNET_BASES else TESTNET_BASES[0]}
SPOT_BASE = "https://api.binance.com"
ALLOWED_HOSTS = {"fapi.binance.com", "demo-fapi.binance.com", "testnet.binancefuture.com", "api.binance.com"}
ALLOWED = {
    ("GET", "/fapi/v1/time"), ("GET", "/fapi/v1/exchangeInfo"), ("GET", "/fapi/v1/klines"),
    ("GET", "/fapi/v2/ticker/price"),
    ("GET", "/fapi/v3/account"), ("GET", "/fapi/v3/positionRisk"), ("GET", "/fapi/v1/positionSide/dual"),
    ("GET", "/fapi/v1/multiAssetsMargin"),
    ("GET", "/fapi/v1/userTrades"),
    ("POST", "/fapi/v1/order"), ("GET", "/fapi/v1/order"),
    ("POST", "/fapi/v1/algoOrder"), ("DELETE", "/fapi/v1/algoOrder"), ("GET", "/fapi/v1/algoOpenOrders"),
    ("POST", "/fapi/v1/leverage"), ("POST", "/fapi/v1/marginType"),
    ("GET", "/sapi/v1/account/apiRestrictions"),
}
# every order the bot sends carries this client-id prefix; orders without it (the user's own) are never
# cancelled or changed. "Cancel all open orders of a symbol" is deliberately not on the allow-list.
BOT_PREFIX = "tsa"
FORBIDDEN_TOKENS = ("withdraw", "transfer", "deposit", "capital", "convert", "sub-account", "subaccount",
                    "loan", "lending", "pay", "asset/", "dust", "margin/")


def bot_id(kind: str) -> str:
    """Client order id of a bot order: tsa + kind + ms + random (Binance: ^[.A-Z:/a-z0-9_-]{1,36}$)."""
    return f"{BOT_PREFIX}{kind}{int(time.time() * 1000)}{random.randint(0, 9999):04d}"


def is_bot_order(o: dict) -> bool:
    cid = str(o.get("clientAlgoId") or o.get("clientOrderId") or "")
    return cid.startswith(BOT_PREFIX)


# ---------------------------------------------------------------- request weights (Binance docs)
def _kline_weight(p: dict) -> int:
    n = int(p.get("limit") or 500)
    return 1 if n < 100 else 2 if n < 500 else 5 if n <= 1000 else 10


WEIGHTS = {
    ("GET", "/fapi/v1/time"): 1, ("GET", "/fapi/v1/exchangeInfo"): 1, ("GET", "/fapi/v1/klines"): _kline_weight,
    ("GET", "/fapi/v2/ticker/price"): lambda p: 1 if p.get("symbol") else 2,
    ("GET", "/fapi/v3/account"): 5, ("GET", "/fapi/v3/positionRisk"): 5, ("GET", "/fapi/v1/positionSide/dual"): 30,
    ("GET", "/fapi/v1/multiAssetsMargin"): 30, ("GET", "/fapi/v1/userTrades"): 5, ("GET", "/fapi/v1/order"): 1,
    ("POST", "/fapi/v1/order"): 0, ("POST", "/fapi/v1/algoOrder"): 0, ("DELETE", "/fapi/v1/algoOrder"): 1,
    ("GET", "/fapi/v1/algoOpenOrders"): lambda p: 1 if p.get("symbol") else 40,
    ("POST", "/fapi/v1/leverage"): 1, ("POST", "/fapi/v1/marginType"): 1,
}
ORDER_PATHS = {("POST", "/fapi/v1/order"), ("POST", "/fapi/v1/algoOrder")}


def weight_of(method: str, path: str, params: dict | None) -> int:
    w = WEIGHTS.get((method, path), 1)
    return int(w(params or {}) if callable(w) else w)


class RateLimiter:
    """Client-side request-weight budget, so the account/IP never reaches Binance's limits.

    Binance USD-M: 2400 weight / minute per IP, 1200 orders / minute and 300 / 10 s per account.
    Normal requests may use at most `per_min` (half of the IP limit); orders that close or protect a
    position may use up to `critical_per_min`. The server's own count (X-MBX-USED-WEIGHT-1M header,
    which also includes other programs on the same IP) is respected as a floor until the minute ends."""

    def __init__(self, per_min: int = 1200, critical_per_min: int = 2000, orders_per_10s: int = 50,
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.per_min, self.critical_per_min, self.orders_per_10s = per_min, critical_per_min, orders_per_10s
        self.clock, self.sleep = clock, sleep
        self.events: deque = deque()          # (t, weight)
        self.orders: deque = deque()          # t
        self.server_used, self.server_until = 0, 0.0
        self.waited_s = 0.0                   # total time spent waiting (shown in the UI)

    def used(self, now: float | None = None) -> int:
        now = self.clock() if now is None else now
        while self.events and self.events[0][0] <= now - 60:
            self.events.popleft()
        local = sum(w for _, w in self.events)
        return max(local, self.server_used if now < self.server_until else 0)

    def _wait_for(self, w: int, cap: int, order: bool, now: float) -> float:
        wait = 0.0
        if self.used(now) + w > cap:
            if self.server_used and now < self.server_until and self.server_used + w > cap:
                wait = self.server_until - now
            acc, need = 0, self.used(now) + w - cap
            for t, ew in self.events:
                acc += ew
                if acc >= need:
                    wait = max(wait, t + 60 - now)
                    break
            else:
                wait = max(wait, 1.0)
        if order:
            while self.orders and self.orders[0] <= now - 10:
                self.orders.popleft()
            if len(self.orders) >= self.orders_per_10s:
                wait = max(wait, self.orders[0] + 10 - now)
        return wait

    async def acquire(self, w: int, critical: bool = False, order: bool = False) -> None:
        cap = self.critical_per_min if critical else self.per_min
        while True:
            now = self.clock()
            wait = self._wait_for(w, cap, order, now)
            if wait <= 0:
                self.events.append((now, w))
                if order:
                    self.orders.append(now)
                return
            wait = min(max(wait, 0.05), 5.0)
            self.waited_s += wait
            await self.sleep(wait)

    def observe(self, headers) -> None:
        v = headers.get("x-mbx-used-weight-1m") if headers is not None else None
        if v is None:
            return
        try:
            used = int(v)
        except (TypeError, ValueError):
            return
        wall = time.time()
        self.server_used = used
        self.server_until = self.clock() + (60 - wall % 60)     # Binance counts per calendar minute


class ExchangeError(Exception):
    def __init__(self, status: int, code, msg: str):
        super().__init__(f"Binance error {status} code={code}: {msg}")
        self.status, self.code, self.msg = status, code, msg


class ForbiddenEndpoint(Exception):
    pass


def check_endpoint(method: str, path: str) -> None:
    low = path.lower()
    if any(t in low for t in FORBIDDEN_TOKENS) or (method.upper(), path) not in ALLOWED:
        raise ForbiddenEndpoint(f"{method} {path} is not on the allow-list")


def num(x: float) -> str:
    """Plain decimal string without exponent or float noise: 1e-05 -> '0.00001', 100.0 -> '100'."""
    d = Decimal(repr(float(x))).normalize()
    return format(d, "f")


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


@dataclass
class SymbolRules:
    symbol: str
    step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    tick: Decimal
    min_notional: float

    def floor_qty(self, qty: float) -> float:
        q = (Decimal(str(qty)) / self.step).to_integral_value(ROUND_DOWN) * self.step
        q = min(q, self.max_qty)
        return float(q) if q >= self.min_qty else 0.0

    def round_price(self, px: float) -> float:
        return float((Decimal(str(px)) / self.tick).to_integral_value() * self.tick)


def parse_rules(info: dict) -> dict[str, SymbolRules]:
    out = {}
    for s in info.get("symbols", []):
        if (s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("marginAsset") != "USDT"
                or s.get("status") != "TRADING"):
            continue
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE")
        out[s["symbol"]] = SymbolRules(
            s["symbol"], Decimal(lot["stepSize"]), Decimal(lot["minQty"]), Decimal(lot["maxQty"]),
            Decimal(f["PRICE_FILTER"]["tickSize"]), float(f.get("MIN_NOTIONAL", {}).get("notional", 5)))
    return out


def klines_frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame([r[:9] for r in rows], columns=["open_time", "open", "high", "low", "close", "volume",
                                                      "close_time", "quote_volume", "count"])
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    return df


class BinanceFutures:
    def __init__(self, venue: str = "live", api_key: Secret | None = None, api_secret: Secret | None = None,
                 http: httpx.AsyncClient | None = None, recv_window: int = 5000):
        if venue not in BASES:
            raise ValueError("venue must be live or testnet")
        self.venue = venue
        self.base = BASES[venue]
        self._key, self._secret = api_key, api_secret
        if api_key:
            REDACT.add(api_key, api_secret)
        # trust_env=False: no proxy / CA overrides from the environment unless the user opts in
        self.http = http or httpx.AsyncClient(timeout=15.0, follow_redirects=False,
                                              trust_env=os.environ.get("TSABOT_TRUST_ENV") == "1",
                                              headers={"User-Agent": "tsabot/1.0"})
        self.recv_window = recv_window
        self.offset_ms = 0
        self.synced_at = 0.0
        self.blocked_until = 0.0
        self.limiter = RateLimiter()
        self.net_errors = 0                   # consecutive network failures (UI: connection state)
        self.last_ok = 0.0

    @property
    def has_keys(self) -> bool:
        return bool(self._key) and bool(self._secret)

    async def close(self):
        await self.http.aclose()

    async def request(self, method: str, path: str, params: dict | None = None, signed: bool = False,
                      base: str | None = None):
        if signed and time.time() - self.synced_at > 1800 and path != "/fapi/v1/time":
            try:                                    # best effort; the -1021 retry below still applies
                await self.sync_time()
            except Exception as e:
                self.synced_at = time.time() - 1500     # retry in ~5 min, keep the old offset
                log.warning("time sync failed: %s", REDACT.clean(str(e))[:200])
        try:
            return await self._request(method, path, params, signed, base)
        except ExchangeError as e:
            if signed and e.code == -1021:          # timestamp outside recvWindow: nothing was executed
                await self.sync_time()
                return await self._request(method, path, params, signed, base)
            raise

    @staticmethod
    def _critical(method: str, path: str, params: dict | None) -> bool:
        """Requests that protect or close positions: they may pass a 429 back-off and use the reserve."""
        if (method, path) in (("POST", "/fapi/v1/algoOrder"), ("DELETE", "/fapi/v1/algoOrder"),
                              ("GET", "/fapi/v1/algoOpenOrders")):
            return True
        return (method, path) == ("POST", "/fapi/v1/order") and (params or {}).get("reduceOnly") == "true"

    _may_pass_backoff = _critical

    RETRY_DELAYS = (0.5, 1.5)

    @staticmethod
    def _retryable(method: str, e: Exception) -> bool:
        """GETs are repeated after any network error or 5xx. Orders only when the request provably never
        reached Binance (connection not established): after a timeout or a 503 the order may have
        been executed, so it is reconciled with the exchange instead of being sent twice."""
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return True
        if method != "GET":
            return False
        if isinstance(e, httpx.TransportError):
            return True
        return isinstance(e, ExchangeError) and (e.status in (500, 502, 503, 504) or e.code == -1001)

    async def _request(self, method: str, path: str, params: dict | None, signed: bool, base: str | None):
        method = method.upper()
        check_endpoint(method, path)
        url_base = base or self.base
        if urlsplit(url_base).hostname not in ALLOWED_HOSTS:
            raise ForbiddenEndpoint(f"host {url_base} is not allowed")
        for attempt in range(len(self.RETRY_DELAYS) + 1):
            try:
                out = await self._send(method, path, params, signed, url_base)
                self.net_errors, self.last_ok = 0, time.time()
                return out
            except (httpx.TransportError, ExchangeError) as e:
                if isinstance(e, httpx.TransportError):
                    self.net_errors += 1
                if attempt >= len(self.RETRY_DELAYS) or not self._retryable(method, e):
                    raise
                await asyncio.sleep(self.RETRY_DELAYS[attempt] * (1 + random.random() * 0.3))

    async def _send(self, method: str, path: str, params: dict | None, signed: bool, url_base: str):
        critical = self._critical(method, path, params)
        now = time.time()
        if now < self.blocked_until and not critical:
            raise ExchangeError(429, None, f"istek limiti: {int(self.blocked_until - now) + 1} sn bekleniyor")
        if url_base != SPOT_BASE:                   # /sapi counts against the separate spot limits
            await self.limiter.acquire(weight_of(method, path, params), critical=critical,
                                       order=(method, path) in ORDER_PATHS)
        p = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not self.has_keys:
                raise ExchangeError(0, None, "API keys are not configured")
            p["recvWindow"] = self.recv_window
            p["timestamp"] = int(time.time() * 1000) + self.offset_ms
            q = urlencode(p)
            q += "&signature=" + sign(self._secret.reveal(), q)
            headers["X-MBX-APIKEY"] = self._key.reveal()
        else:
            q = urlencode(p)
        r = await self.http.request(method, f"{url_base}{path}" + (f"?{q}" if q else ""), headers=headers)
        self.limiter.observe(r.headers)
        if r.status_code in (418, 429):
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = 120.0 if r.status_code == 418 else 60.0
            self.blocked_until = time.time() + max(wait, 1.0)
        if r.status_code >= 400:
            try:
                j = r.json()
                code, msg = j.get("code"), str(j.get("msg", ""))[:300]
            except Exception:
                code, msg = None, r.text[:200]
            raise ExchangeError(r.status_code, code, REDACT.clean(msg))
        return r.json()

    # ---------------------------------------------------------------- public
    async def sync_time(self) -> int:
        t0 = int(time.time() * 1000)
        srv = (await self.request("GET", "/fapi/v1/time"))["serverTime"]
        t1 = int(time.time() * 1000)
        self.offset_ms = int(srv - (t0 + t1) / 2)
        self.synced_at = time.time()
        return self.offset_ms

    async def exchange_info(self) -> dict[str, SymbolRules]:
        return parse_rules(await self.request("GET", "/fapi/v1/exchangeInfo"))

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        return klines_frame(await self.request("GET", "/fapi/v1/klines",
                                               {"symbol": symbol, "interval": interval, "limit": limit}))

    async def price(self, symbol: str) -> float:
        return float((await self.request("GET", "/fapi/v2/ticker/price", {"symbol": symbol}))["price"])

    async def prices(self) -> dict[str, float]:
        """Last price of every symbol in one request (weight 2)."""
        return {x["symbol"]: float(x["price"]) for x in await self.request("GET", "/fapi/v2/ticker/price")}

    # ---------------------------------------------------------------- signed
    async def api_restrictions(self) -> dict:
        """Key permissions (live only). Used to refuse keys that can withdraw/transfer."""
        return await self.request("GET", "/sapi/v1/account/apiRestrictions", signed=True, base=SPOT_BASE)

    async def account(self) -> dict:
        return await self.request("GET", "/fapi/v3/account", signed=True)

    async def dual_side(self) -> bool:
        return bool((await self.request("GET", "/fapi/v1/positionSide/dual", signed=True))["dualSidePosition"])

    async def positions(self, symbol: str | None = None) -> list[dict]:
        return await self.request("GET", "/fapi/v3/positionRisk", {"symbol": symbol}, signed=True)

    async def set_leverage(self, symbol: str, leverage: int):
        return await self.request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, True)

    async def set_isolated(self, symbol: str):
        try:
            return await self.request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"},
                                      True)
        except ExchangeError as e:
            if e.code == -4046:  # already isolated
                return None
            if e.code == -4168:
                raise ExchangeError(e.status, e.code, "Hesap Multi-Assets modunda; izole marjin için Binance Futures "
                                                      "ayarlarından Single-Asset moda geçin.") from None
            raise

    async def market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False,
                           client_id: str | None = None) -> dict:
        return await self.request("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": num(qty),
            "reduceOnly": "true" if reduce_only else None, "newClientOrderId": client_id or bot_id("O"),
            "newOrderRespType": "RESULT"}, True)

    async def order(self, symbol: str, order_id: int) -> dict:
        return await self.request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, True)

    async def conditional_close(self, symbol: str, side: str, kind: str, trigger: float,
                                qty: float | None = None) -> dict:
        """Exchange-side TP/SL (algo order API, mandatory since 2025-12-09).

        With `qty` the order is reduce-only for exactly the bot's quantity, so a position the user adds
        by hand on the same coin is never closed by the bot's stop."""
        if kind not in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
            raise ValueError("only STOP_MARKET / TAKE_PROFIT_MARKET close orders are used")
        p = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": kind,
             "triggerPrice": num(trigger), "workingType": "CONTRACT_PRICE",
             "priceProtect": "false" if kind == "STOP_MARKET" else "true",
             "clientAlgoId": bot_id("S" if kind == "STOP_MARKET" else "T")}
        if qty is not None:
            p.update(quantity=num(qty), reduceOnly="true")
        else:
            p["closePosition"] = "true"
        return await self.request("POST", "/fapi/v1/algoOrder", p, True)

    async def open_conditionals(self, symbol: str) -> list[dict]:
        return await self.request("GET", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, True)

    async def multi_assets(self) -> bool:
        return bool((await self.request("GET", "/fapi/v1/multiAssetsMargin", signed=True))["multiAssetsMargin"])

    async def cancel_algo(self, symbol: str, algo_id) -> None:
        await self.request("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id}, True)

    async def cancel_conditionals(self, symbol: str, only=None) -> int:
        """Cancels the BOT's open conditional orders of a symbol (never the user's own). Returns the count."""
        n = 0
        try:
            orders = await self.open_conditionals(symbol)
        except ExchangeError as e:
            log.warning("list conditionals %s: %s", symbol, e.msg)
            return 0
        for o in orders:
            if not is_bot_order(o) or (only is not None and not only(o)):
                continue
            try:
                await self.cancel_algo(symbol, o.get("algoId"))
                n += 1
            except ExchangeError as e:
                log.warning("cancel conditional %s %s: %s", symbol, o.get("algoId"), e.msg)
        return n

    async def user_trades(self, symbol: str, start_ms: int) -> list[dict]:
        return await self.request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "startTime": start_ms,
                                                                 "limit": 100}, True)
