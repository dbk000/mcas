"""
Binance Spot REST + WebSocket client — live / testnet only, no paper mode.

Phase D of ENFORCER_FIRST_DEPLOYMENT_PLAN_20260412. Used by
``governance.execution_worker`` to place, cancel, and track live orders
submitted through the Cap3 pipeline.

Key design points
-----------------
* Endpoints are env-driven:
    BINANCE_MODE ∈ {"live", "testnet"}
    live    → https://api.binance.com         wss://stream.binance.com:9443
    testnet → https://testnet.binance.vision  wss://stream.testnet.binance.vision
* Auth: HMAC-SHA256 over the query string; API key in the ``X-MBX-APIKEY``
  header. ``BINANCE_API_KEY`` / ``BINANCE_API_SECRET`` from environment.
* Order submission is idempotent — every POST carries a caller-supplied
  ``newClientOrderId``. The integration tests and smoke tests rely on this
  to safely retry without doubling up.
* LIMIT_MAKER is preferred (post-only, maker fee). If Binance rejects with
  ``-2010`` (would cross the book), we fall back to a plain ``LIMIT`` with
  a 5 bps price adjustment away from the touch.
* 429 handling: exponential backoff (1s → 4s → 16s) for up to 3 retries.
  Anything else fails loud.
* WebSocket: POST ``/api/v3/userDataStream`` → listenKey; connect to
  ``<base>/ws/<listenKey>``; keepalive via PUT every 30 min. Fill events
  arrive as ``executionReport``. The handler simply invokes a
  user-supplied callback — the execution worker aggregates them into
  ``live_positions.fills``.

This module has **no** governance imports. It is a thin transport layer.
Risk checks, DB writes, and aggregation live one layer up in
``governance.execution_worker``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

try:
    import websocket  # type: ignore  # websocket-client (sync)
except Exception:  # pragma: no cover - optional at import time
    websocket = None  # noqa: N816

logger = logging.getLogger("binance_client")


# --------------------------------------------------------------------------- #
# Enums / constants
# --------------------------------------------------------------------------- #
class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"
    MARKET = "MARKET"


RECV_WINDOW_MS = 5000
DEFAULT_TIMEOUT_S = 10
MAX_RETRIES = 3
RETRY_BACKOFF_S = (1, 4, 16)
# Binance rejects a LIMIT_MAKER that would immediately cross.
LIMIT_MAKER_CROSSED_CODE = -2010
# Fallback price adjustment (5 bps) for LIMIT retry.
LIMIT_FALLBACK_BPS = 5.0


# --------------------------------------------------------------------------- #
# Exceptions — explicit types so the execution worker can branch on them.
# --------------------------------------------------------------------------- #
class BinanceClientError(Exception):
    """Base class for all Binance client errors."""


class BinanceAuthError(BinanceClientError):
    """Missing credentials or -2014 / -2015."""


class BinanceNetworkError(BinanceClientError):
    """DNS, connection refused, timeouts, SSL failures."""


class BinanceAPIError(BinanceClientError):
    """Non-retryable API error (bad symbol, filter failure, etc)."""

    def __init__(self, code: int, msg: str, raw: Optional[Dict[str, Any]] = None):
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg
        self.raw = raw or {}


class BinanceRateLimitError(BinanceClientError):
    """429 / 418 exhausted after retries."""


# --------------------------------------------------------------------------- #
# Response records
# --------------------------------------------------------------------------- #
@dataclass
class BinanceOrder:
    symbol: str
    order_id: int
    client_order_id: str
    side: str
    order_type: str
    price: Optional[float]
    orig_qty: float
    executed_qty: float
    status: str
    transact_ts: int
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class BinanceListenKey:
    listen_key: str
    created_ts: int


# --------------------------------------------------------------------------- #
# Endpoint resolver
# --------------------------------------------------------------------------- #
def _resolve_endpoints(mode: str) -> Dict[str, str]:
    """Map ``BINANCE_MODE`` → (rest_base, ws_base).

    Raises
    ------
    ValueError: unknown mode (paper, mainnet-real, etc.). There is NO paper
        path in production — that lives in ``governance.paper_trading``.
    """
    m = (mode or "").lower()
    if m == "live":
        return {
            "rest": "https://api.binance.com",
            "ws": "wss://stream.binance.com:9443",
        }
    if m == "testnet":
        return {
            "rest": "https://testnet.binance.vision",
            "ws": "wss://stream.testnet.binance.vision",
        }
    raise ValueError(
        f"BINANCE_MODE must be 'live' or 'testnet' (got {mode!r}); "
        f"there is no paper path — see governance.paper_trading"
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class BinanceClient:
    """Authenticated Binance spot client (REST + user-data WebSocket).

    Parameters
    ----------
    api_key, api_secret : str | None
        Credentials. If either is None, they are read from
        ``BINANCE_API_KEY`` / ``BINANCE_API_SECRET``.
    mode : str | None
        ``"live"`` or ``"testnet"``. Defaults to ``BINANCE_MODE`` env var.
    rest_base, ws_base : str | None
        Override the computed endpoints — used by tests.
    timeout_s : int
        Per-request timeout. Default 10 s.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        mode: Optional[str] = None,
        rest_base: Optional[str] = None,
        ws_base: Optional[str] = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ):
        self.api_key = api_key if api_key is not None else os.environ.get("BINANCE_API_KEY")
        self.api_secret = (
            api_secret if api_secret is not None else os.environ.get("BINANCE_API_SECRET")
        )
        self.mode = (mode or os.environ.get("BINANCE_MODE", "testnet")).lower()

        endpoints = _resolve_endpoints(self.mode)
        self.rest_base = rest_base or endpoints["rest"]
        self.ws_base = ws_base or endpoints["ws"]
        self.timeout_s = timeout_s

        # Lightweight local rate-limit (10 req/s) to avoid tripping weight caps
        # on bursty placement. The real weight accounting lives server-side.
        self._min_interval_s = 0.1
        self._last_req_ts = 0.0

        # WebSocket state (private stream)
        self._ws_app: Any = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_listen_key: Optional[str] = None
        self._ws_stop_event = threading.Event()
        self._ws_keepalive_thread: Optional[threading.Thread] = None

    # ── Auth ────────────────────────────────────────────────────────────── #

    def _require_creds(self) -> None:
        if not self.api_key or not self.api_secret:
            raise BinanceAuthError(
                "BINANCE_API_KEY/SECRET not set — refusing to authenticate"
            )

    def _sign(self, query: str) -> str:
        assert self.api_secret is not None
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signed_qs(self, params: Dict[str, Any]) -> str:
        """Append timestamp + recvWindow, then sign, return the full query string."""
        self._require_creds()
        params = dict(params)
        params.setdefault("recvWindow", RECV_WINDOW_MS)
        params["timestamp"] = int(time.time() * 1000)
        qs = urllib.parse.urlencode(params)
        sig = self._sign(qs)
        return f"{qs}&signature={sig}"

    def _rate_wait(self) -> None:
        now = time.time()
        wait = self._min_interval_s - (now - self._last_req_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_req_ts = time.time()

    # ── Transport ───────────────────────────────────────────────────────── #

    def _send(
        self,
        method: str,
        path: str,
        *,
        signed: bool,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Issue an HTTP request with retry-on-429 and HMAC signing.

        ``params`` is query-string in every case (Binance spot API convention,
        including signed POSTs — the body stays empty and everything rides in
        the query string).
        """
        params = params or {}
        self._rate_wait()

        if signed:
            query = self._signed_qs(params)
        else:
            query = urllib.parse.urlencode(params)

        url = f"{self.rest_base}{path}"
        if query:
            url = f"{url}?{query}"

        headers: Dict[str, str] = {"User-Agent": "MCAS/1.0"}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        req = urllib.request.Request(url, method=method, headers=headers)

        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    body = resp.read()
                    if not body:
                        return {}
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                body_raw = b""
                try:
                    body_raw = e.read()
                except Exception:  # noqa: BLE001
                    pass
                body_text = body_raw.decode("utf-8", errors="replace") if body_raw else ""
                try:
                    payload = json.loads(body_text) if body_text else {}
                except json.JSONDecodeError:
                    payload = {"code": e.code, "msg": body_text}

                if e.code in (429, 418):
                    if attempt < MAX_RETRIES - 1:
                        backoff = RETRY_BACKOFF_S[attempt]
                        logger.warning(
                            "Binance %d — backing off %ss (attempt %d/%d)",
                            e.code, backoff, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(backoff)
                        continue
                    raise BinanceRateLimitError(
                        f"HTTP {e.code} after {MAX_RETRIES} retries: {body_text}"
                    ) from e

                if e.code in (401, 403):
                    raise BinanceAuthError(
                        f"HTTP {e.code}: {payload.get('msg', body_text)}"
                    ) from e

                # Non-retryable API error — fail loud with structured code/msg.
                code = payload.get("code") or -1
                msg = payload.get("msg") or body_text or str(e)
                raise BinanceAPIError(int(code), msg, raw=payload) from e

            except urllib.error.URLError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_S[attempt])
                    continue
                raise BinanceNetworkError(
                    f"URLError after {MAX_RETRIES} attempts: {e}"
                ) from e

            except (TimeoutError, ConnectionError) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_S[attempt])
                    continue
                raise BinanceNetworkError(
                    f"Network error after {MAX_RETRIES} attempts: {e}"
                ) from e

        # Defensive — loop always returns or raises above, but mypy likes this.
        raise BinanceNetworkError(f"exhausted retries; last_error={last_error}")

    # ── Public endpoints ───────────────────────────────────────────────── #

    def ping(self) -> Dict[str, Any]:
        """Sanity check — ``GET /api/v3/ping``."""
        return self._send("GET", "/api/v3/ping", signed=False)

    def server_time(self) -> int:
        data = self._send("GET", "/api/v3/time", signed=False)
        return int(data.get("serverTime", 0))

    def exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = {"symbol": symbol} if symbol else {}
        return self._send("GET", "/api/v3/exchangeInfo", signed=False, params=params)

    def book_ticker(self, symbol: str) -> Dict[str, Any]:
        return self._send(
            "GET", "/api/v3/ticker/bookTicker", signed=False, params={"symbol": symbol}
        )

    # ── Account ────────────────────────────────────────────────────────── #

    def account(self) -> Dict[str, Any]:
        return self._send("GET", "/api/v3/account", signed=True)

    # ── Orders ─────────────────────────────────────────────────────────── #

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        *,
        qty: float,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None,
        time_in_force: str = "GTC",
        quote_order_qty: Optional[float] = None,
        fallback_to_limit: bool = True,
    ) -> BinanceOrder:
        """Submit a spot order.

        LIMIT_MAKER rejections (``-2010``) fall back to plain ``LIMIT`` with
        a 5 bps adjustment if ``fallback_to_limit`` is True.
        """
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.value if isinstance(side, OrderSide) else side,
            "type": order_type.value if isinstance(order_type, OrderType) else order_type,
        }
        if qty is not None:
            params["quantity"] = f"{qty:.8f}".rstrip("0").rstrip(".") or "0"
        if quote_order_qty is not None:
            params["quoteOrderQty"] = f"{quote_order_qty:.8f}".rstrip("0").rstrip(".")

        if order_type in (OrderType.LIMIT, OrderType.LIMIT_MAKER):
            if price is None:
                raise ValueError("price required for LIMIT / LIMIT_MAKER orders")
            params["price"] = f"{price:.8f}".rstrip("0").rstrip(".") or "0"

        if order_type == OrderType.LIMIT:
            params["timeInForce"] = time_in_force

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        try:
            data = self._send("POST", "/api/v3/order", signed=True, params=params)
            return self._mk_order(data)
        except BinanceAPIError as exc:
            if (
                fallback_to_limit
                and order_type == OrderType.LIMIT_MAKER
                and exc.code == LIMIT_MAKER_CROSSED_CODE
                and price is not None
            ):
                logger.warning(
                    "LIMIT_MAKER crossed book on %s — falling back to LIMIT with %sbp adjust",
                    symbol, LIMIT_FALLBACK_BPS,
                )
                side_val = side.value if isinstance(side, OrderSide) else side
                adj = LIMIT_FALLBACK_BPS / 10_000.0
                # Pull price away from the touch so a straightforward LIMIT
                # won't cross either — less aggressive than marketable-limit,
                # preserves maker intent directionally.
                new_price = price * (1 - adj) if side_val == "BUY" else price * (1 + adj)
                return self.place_order(
                    symbol,
                    side,
                    OrderType.LIMIT,
                    qty=qty,
                    price=new_price,
                    client_order_id=client_order_id,
                    time_in_force=time_in_force,
                    fallback_to_limit=False,
                )
            raise

    def cancel_order(
        self,
        symbol: str,
        *,
        client_order_id: Optional[str] = None,
        order_id: Optional[int] = None,
    ) -> BinanceOrder:
        """Cancel by either clientOrderId or exchange orderId."""
        if not client_order_id and not order_id:
            raise ValueError("cancel requires client_order_id or order_id")
        params: Dict[str, Any] = {"symbol": symbol.upper()}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        if order_id:
            params["orderId"] = order_id
        data = self._send("DELETE", "/api/v3/order", signed=True, params=params)
        return self._mk_order(data)

    def get_order(
        self,
        symbol: str,
        *,
        client_order_id: Optional[str] = None,
        order_id: Optional[int] = None,
    ) -> BinanceOrder:
        params: Dict[str, Any] = {"symbol": symbol.upper()}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        if order_id:
            params["orderId"] = order_id
        data = self._send("GET", "/api/v3/order", signed=True, params=params)
        return self._mk_order(data)

    @staticmethod
    def _mk_order(data: Dict[str, Any]) -> BinanceOrder:
        return BinanceOrder(
            symbol=data.get("symbol", ""),
            order_id=int(data.get("orderId", 0)),
            client_order_id=data.get("clientOrderId") or data.get("origClientOrderId") or "",
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            price=float(data["price"]) if data.get("price") not in (None, "0", "0.00000000") else None,
            orig_qty=float(data.get("origQty", 0) or 0),
            executed_qty=float(data.get("executedQty", 0) or 0),
            status=data.get("status", ""),
            transact_ts=int(data.get("transactTime") or data.get("updateTime") or 0),
            raw=data,
        )

    # ── User Data Stream (listenKey) ───────────────────────────────────── #

    def start_user_data_stream(self) -> BinanceListenKey:
        """``POST /api/v3/userDataStream`` → returns a listenKey valid 60 min."""
        data = self._send("POST", "/api/v3/userDataStream", signed=False)
        key = data.get("listenKey")
        if not key:
            raise BinanceAPIError(-1, f"no listenKey in response: {data}")
        return BinanceListenKey(listen_key=key, created_ts=int(time.time()))

    def keepalive_user_data_stream(self, listen_key: str) -> None:
        self._send(
            "PUT", "/api/v3/userDataStream", signed=False, params={"listenKey": listen_key}
        )

    def close_user_data_stream(self, listen_key: str) -> None:
        self._send(
            "DELETE", "/api/v3/userDataStream", signed=False, params={"listenKey": listen_key}
        )

    # ── User Data Stream (WebSocket) ───────────────────────────────────── #

    def start_ws_user_stream(
        self,
        on_event: Callable[[Dict[str, Any]], None],
        *,
        on_error: Optional[Callable[[Exception], None]] = None,
        keepalive_interval_s: int = 1800,
    ) -> str:
        """Open a user-data websocket and start the 30-min keepalive.

        ``on_event`` receives the parsed JSON dict for every frame
        (executionReport, outboundAccountPosition, balanceUpdate, etc.).
        The execution worker filters for ``e == "executionReport"``.

        Returns the listenKey — callers that need to retain it for explicit
        close() later can stash it. Use ``stop_ws_user_stream()`` to tear
        down cleanly.
        """
        if websocket is None:
            raise RuntimeError(
                "websocket-client not installed — add to requirements-strats-evals.txt"
            )

        lk = self.start_user_data_stream()
        self._ws_listen_key = lk.listen_key
        self._ws_stop_event.clear()

        ws_url = f"{self.ws_base}/ws/{lk.listen_key}"

        def _on_message(ws, msg):  # noqa: ANN001
            try:
                evt = json.loads(msg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws parse error: %s", exc)
                return
            try:
                on_event(evt)
            except Exception as exc:  # noqa: BLE001
                logger.exception("ws handler raised: %s", exc)

        def _on_error(ws, err):  # noqa: ANN001
            logger.warning("ws error: %s", err)
            if on_error:
                try:
                    on_error(err)
                except Exception:  # noqa: BLE001
                    pass

        def _on_close(ws, code, msg):  # noqa: ANN001
            logger.info("ws closed: code=%s msg=%s", code, msg)

        self._ws_app = websocket.WebSocketApp(
            ws_url,
            on_message=_on_message,
            on_error=_on_error,
            on_close=_on_close,
        )

        def _run():
            try:
                self._ws_app.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws run_forever crashed: %s", exc)

        self._ws_thread = threading.Thread(target=_run, name="binance-ws", daemon=True)
        self._ws_thread.start()

        def _keepalive():
            while not self._ws_stop_event.is_set():
                # Sleep in short increments so stop_event is responsive.
                for _ in range(keepalive_interval_s):
                    if self._ws_stop_event.is_set():
                        return
                    time.sleep(1)
                if self._ws_listen_key:
                    try:
                        self.keepalive_user_data_stream(self._ws_listen_key)
                        logger.info("ws keepalive ok (listenKey refreshed)")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("keepalive failed: %s", exc)

        self._ws_keepalive_thread = threading.Thread(
            target=_keepalive, name="binance-ws-keepalive", daemon=True
        )
        self._ws_keepalive_thread.start()

        return lk.listen_key

    def stop_ws_user_stream(self) -> None:
        """Tear down WS + keepalive + close listenKey."""
        self._ws_stop_event.set()
        try:
            if self._ws_app is not None:
                self._ws_app.close()
        except Exception:  # noqa: BLE001
            pass
        if self._ws_listen_key:
            try:
                self.close_user_data_stream(self._ws_listen_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("close_user_data_stream failed: %s", exc)
        self._ws_listen_key = None
        self._ws_app = None


# --------------------------------------------------------------------------- #
# Partial-fill aggregation — pure helper, tested without the network.
# --------------------------------------------------------------------------- #
def aggregate_execution_reports(
    existing_fills: list, reports: list
) -> Dict[str, Any]:
    """Fold a list of ``executionReport`` events into a position record.

    Binance fill events arrive as (simplified):
        {
            "e": "executionReport",
            "s": "BTCUSDT",
            "c": "<clientOrderId>",
            "x": "TRADE",      # execution type
            "X": "PARTIALLY_FILLED" | "FILLED" | "NEW" | "CANCELED" | ...
            "l": "0.0001",     # last executed qty
            "L": "65000.12",   # last executed price
            "n": "0.00001",    # commission
            "N": "BNB",        # commission asset
            "T": 1234567890000 # transact time (ms)
            "i": 1234,         # orderId
            "Z": "6.5000",     # cumulative quote qty
            "z": "0.0001",     # cumulative filled qty
        }

    Returns a dict with keys:
        ``fills``           (list) — existing + appended trade records
        ``fill_qty``        (float)
        ``avg_fill_price``  (float | None)
        ``last_status``     (str)

    Only events where ``x == "TRADE"`` contribute to fills; others update
    status only.
    """
    fills = list(existing_fills or [])
    total_qty = sum(float(f.get("qty", 0) or 0) for f in fills)
    total_notional = sum(
        float(f.get("qty", 0) or 0) * float(f.get("price", 0) or 0) for f in fills
    )
    last_status = ""

    for r in reports:
        etype = r.get("e")
        if etype != "executionReport":
            continue
        last_status = r.get("X", last_status) or last_status
        if r.get("x") != "TRADE":
            continue
        try:
            q = float(r.get("l", 0))
            p = float(r.get("L", 0))
        except (TypeError, ValueError):
            continue
        if q <= 0:
            continue
        fills.append(
            {
                "ts": int(r.get("T", 0)) // 1000,
                "qty": q,
                "price": p,
                "fee": float(r.get("n", 0) or 0),
                "fee_asset": r.get("N") or "",
                "exchange_trade_id": r.get("t"),
            }
        )
        total_qty += q
        total_notional += q * p

    avg = (total_notional / total_qty) if total_qty > 0 else None
    return {
        "fills": fills,
        "fill_qty": total_qty,
        "avg_fill_price": avg,
        "last_status": last_status,
    }


__all__ = [
    "BinanceClient",
    "BinanceOrder",
    "BinanceListenKey",
    "BinanceClientError",
    "BinanceAuthError",
    "BinanceNetworkError",
    "BinanceAPIError",
    "BinanceRateLimitError",
    "OrderSide",
    "OrderType",
    "aggregate_execution_reports",
]
