"""
Bybit V5 REST API client — authenticated spot trading.

Handles HMAC-SHA256 signing, rate limiting, error handling.
Supports paper mode (log orders without sending) by default.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field

logger = logging.getLogger("bybit_client")

BASE_URL = "https://api.bybit.com"
RECV_WINDOW = "5000"


@dataclass
class BybitOrder:
    order_id: str
    symbol: str
    side: str
    qty: float
    price: float | None
    status: str
    order_type: str
    raw: dict = field(default_factory=dict, repr=False)


class BybitClient:
    """Authenticated Bybit V5 spot client."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self._last_req = 0.0
        self._min_interval = 0.1  # 10 req/s

    # ── Auth ──

    def _sign(self, timestamp: str, payload: str) -> str:
        raw = f"{timestamp}{self.api_key}{RECV_WINDOW}{payload}"
        return hmac.new(
            self.api_secret.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()

    def _headers(self, timestamp: str, signature: str) -> dict:
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "Content-Type": "application/json",
        }

    def _rate_wait(self):
        now = time.time()
        wait = self._min_interval - (now - self._last_req)
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.time()

    # ── HTTP ──

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._rate_wait()
        qs = urllib.parse.urlencode(params) if params else ""
        ts = str(int(time.time() * 1000))
        sig = self._sign(ts, qs)
        url = f"{BASE_URL}{path}?{qs}" if qs else f"{BASE_URL}{path}"
        req = urllib.request.Request(url, headers=self._headers(ts, sig))
        return self._send(req)

    def _post(self, path: str, body: dict) -> dict:
        self._rate_wait()
        body_str = json.dumps(body, separators=(",", ":"))
        ts = str(int(time.time() * 1000))
        sig = self._sign(ts, body_str)
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=body_str.encode(),
            headers=self._headers(ts, sig),
            method="POST",
        )
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> dict:
        for attempt in range(3):
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())
                if data.get("retCode") != 0:
                    logger.warning("API error: %s", data.get("retMsg"))
                return data
            except urllib.error.HTTPError as e:
                body = e.read().decode() if e.fp else ""
                logger.warning("HTTP %d: %s (attempt %d)", e.code, body, attempt + 1)
                if e.code in (429, 418):
                    time.sleep(2 ** attempt)
                    continue
                return {"retCode": e.code, "retMsg": body}
            except Exception as e:
                logger.warning("Request failed: %s (attempt %d)", e, attempt + 1)
                if attempt < 2:
                    time.sleep(1)
        return {"retCode": -1, "retMsg": "max retries"}

    # ── Account ──

    def get_wallet_balance(self, coin: str = "USDC") -> dict:
        """Get unified account balance."""
        data = self._get("/v5/account/wallet-balance", {
            "accountType": "UNIFIED",
            "coin": coin,
        })
        result = data.get("result", {})
        accounts = result.get("list", [])
        if not accounts:
            return {"total": 0, "available": 0, "coin": coin}
        coins = accounts[0].get("coin", [])
        for c in coins:
            if c.get("coin") == coin:
                return {
                    "total": float(c.get("walletBalance", 0)),
                    "available": float(c.get("availableToWithdraw", 0)),
                    "coin": coin,
                }
        return {"total": 0, "available": 0, "coin": coin}

    # ── Orders ──

    def market_buy(self, symbol: str, qty: str,
                   tp_price: str | None = None,
                   sl_price: str | None = None) -> BybitOrder | None:
        """Place a spot market buy. Returns order or None on failure."""
        body = {
            "category": "spot",
            "symbol": symbol,
            "side": "Buy",
            "orderType": "Market",
            "qty": qty,
            "timeInForce": "GTC",
            "marketUnit": "quoteCoin",  # qty is in quote currency (USDC)
        }
        if sl_price:
            body["stopLoss"] = sl_price
            body["slTriggerBy"] = "LastPrice"
        if tp_price:
            body["takeProfit"] = tp_price
            body["tpTriggerBy"] = "LastPrice"

        if self.paper:
            logger.info("[PAPER] BUY %s qty=%s sl=%s", symbol, qty, sl_price)
            return BybitOrder(
                order_id=f"paper_{int(time.time()*1000)}",
                symbol=symbol, side="Buy", qty=float(qty),
                price=None, status="PAPER", order_type="Market",
            )

        data = self._post("/v5/order/create", body)
        if data.get("retCode") != 0:
            logger.error("Buy failed %s: %s", symbol, data.get("retMsg"))
            return None
        r = data.get("result", {})
        return BybitOrder(
            order_id=r.get("orderId", ""),
            symbol=symbol, side="Buy", qty=float(qty),
            price=None, status="Created", order_type="Market", raw=r,
        )

    def market_sell(self, symbol: str, qty: str) -> BybitOrder | None:
        """Place a spot market sell (close position)."""
        body = {
            "category": "spot",
            "symbol": symbol,
            "side": "Sell",
            "orderType": "Market",
            "qty": qty,
            "timeInForce": "GTC",
            "marketUnit": "baseCoin",  # qty is in base currency
        }

        if self.paper:
            logger.info("[PAPER] SELL %s qty=%s", symbol, qty)
            return BybitOrder(
                order_id=f"paper_{int(time.time()*1000)}",
                symbol=symbol, side="Sell", qty=float(qty),
                price=None, status="PAPER", order_type="Market",
            )

        data = self._post("/v5/order/create", body)
        if data.get("retCode") != 0:
            logger.error("Sell failed %s: %s", symbol, data.get("retMsg"))
            return None
        r = data.get("result", {})
        return BybitOrder(
            order_id=r.get("orderId", ""),
            symbol=symbol, side="Sell", qty=float(qty),
            price=None, status="Created", order_type="Market", raw=r,
        )

    def cancel_all_orders(self, symbol: str) -> bool:
        """Cancel all open orders for a symbol."""
        if self.paper:
            logger.info("[PAPER] CANCEL ALL %s", symbol)
            return True
        data = self._post("/v5/order/cancel-all", {
            "category": "spot", "symbol": symbol,
        })
        return data.get("retCode") == 0

    # ── Market data (public, no auth needed) ──

    def get_tickers(self, symbols: list[str] | None = None) -> dict[str, float]:
        """Get last prices for spot symbols. Returns {symbol: price}."""
        params = {"category": "spot"}
        data = self._get("/v5/market/tickers", params)
        tickers = {}
        for t in data.get("result", {}).get("list", []):
            sym = t.get("symbol", "")
            if symbols and sym not in symbols:
                continue
            lp = t.get("lastPrice")
            if lp:
                tickers[sym] = float(lp)
        return tickers

    def get_klines(self, symbol: str, interval: str = "60",
                   limit: int = 200) -> list[dict]:
        """Get recent klines (public endpoint)."""
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": interval,
            "limit": str(limit),
        }
        # Public endpoint — no auth needed, but we use the same _get for rate limiting
        self._rate_wait()
        qs = urllib.parse.urlencode(params)
        url = f"{BASE_URL}/v5/market/kline?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "MCAS/1.0"})
        data = self._send(req)
        raw = data.get("result", {}).get("list", [])
        candles = []
        for r in raw:
            candles.append({
                "ts": int(r[0]),
                "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]),
                "volume": float(r[5]),
            })
        candles.sort(key=lambda c: c["ts"])
        return candles

    def get_instruments(self, symbol: str = None) -> list[dict]:
        """Get spot instrument info (lot sizes, min order, tick size)."""
        params = {"category": "spot"}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/v5/market/instruments-info", params)
        return data.get("result", {}).get("list", [])
