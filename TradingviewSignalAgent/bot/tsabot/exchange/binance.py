"""Minimal Binance USDT-M futures REST client with a hard endpoint allow-list.

Nothing outside ALLOWED can be called: there is no code path to wallet endpoints
(withdraw, transfer, deposit, convert, ...). The only non-futures call is the
read-only GET /sapi/v1/account/apiRestrictions, used to REFUSE live trading when
the key has withdrawal or transfer permissions.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
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
    ("GET", "/fapi/v1/ticker/price"),
    ("GET", "/fapi/v3/account"), ("GET", "/fapi/v3/positionRisk"), ("GET", "/fapi/v1/positionSide/dual"),
    ("GET", "/fapi/v1/userTrades"),
    ("POST", "/fapi/v1/order"), ("GET", "/fapi/v1/order"),
    ("POST", "/fapi/v1/algoOrder"), ("DELETE", "/fapi/v1/algoOrder"),
    ("DELETE", "/fapi/v1/algoOpenOrders"), ("GET", "/fapi/v1/openAlgoOrders"),
    ("POST", "/fapi/v1/leverage"), ("POST", "/fapi/v1/marginType"),
    ("GET", "/sapi/v1/account/apiRestrictions"),
}
FORBIDDEN_TOKENS = ("withdraw", "transfer", "deposit", "capital", "convert", "sub-account", "subaccount",
                    "loan", "lending", "pay", "asset/", "dust", "margin/")


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

    @property
    def has_keys(self) -> bool:
        return bool(self._key) and bool(self._secret)

    async def close(self):
        await self.http.aclose()

    async def request(self, method: str, path: str, params: dict | None = None, signed: bool = False,
                      base: str | None = None):
        method = method.upper()
        check_endpoint(method, path)
        url_base = base or self.base
        if urlsplit(url_base).hostname not in ALLOWED_HOSTS:
            raise ForbiddenEndpoint(f"host {url_base} is not allowed")
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
        return self.offset_ms

    async def exchange_info(self) -> dict[str, SymbolRules]:
        return parse_rules(await self.request("GET", "/fapi/v1/exchangeInfo"))

    async def klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        return klines_frame(await self.request("GET", "/fapi/v1/klines",
                                               {"symbol": symbol, "interval": interval, "limit": limit}))

    async def price(self, symbol: str) -> float:
        return float((await self.request("GET", "/fapi/v1/ticker/price", {"symbol": symbol}))["price"])

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
            raise

    async def market_order(self, symbol: str, side: str, qty: float, reduce_only: bool = False,
                           client_id: str | None = None) -> dict:
        return await self.request("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": f"{qty:f}".rstrip("0").rstrip("."),
            "reduceOnly": "true" if reduce_only else None, "newClientOrderId": client_id,
            "newOrderRespType": "RESULT"}, True)

    async def conditional_close(self, symbol: str, side: str, kind: str, trigger: float) -> dict:
        """Exchange-side TP/SL (algo order API, mandatory since 2025-12-09): closes the whole position."""
        if kind not in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
            raise ValueError("only STOP_MARKET / TAKE_PROFIT_MARKET close orders are used")
        return await self.request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": kind,
            "triggerPrice": f"{trigger:f}".rstrip("0").rstrip("."), "closePosition": "true",
            "workingType": "CONTRACT_PRICE", "priceProtect": "true"}, True)

    async def cancel_conditionals(self, symbol: str):
        try:
            return await self.request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, True)
        except ExchangeError as e:
            log.warning("cancel conditionals %s: %s", symbol, e.msg)
            return None

    async def user_trades(self, symbol: str, start_ms: int) -> list[dict]:
        return await self.request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "startTime": start_ms,
                                                                 "limit": 100}, True)
