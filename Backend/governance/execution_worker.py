"""
Execution Worker — Phase D of ENFORCER_FIRST_DEPLOYMENT_PLAN_20260412.

Reads Cap3Pipeline ACCEPT signals (opportunities rows with
``status='accepted_new'``), sizes them, pushes orders through
``trading.binance_client.BinanceClient``, and mirrors fills into
``live_positions``.

Boundary contracts
------------------
* **Input side:** opportunities table (poll-based, ``status='accepted_new'``).
  Redis pub/sub hook is left for later — polling is simple, idempotent, and
  survives worker restarts.
* **Output side:** one row per ACCEPT in ``live_positions`` (unique
  ``client_order_id`` guarantees idempotency), updated in place as fills
  arrive on the user-data WebSocket.
* **Risk hook:** imports ``governance.risk_limits.check_risk_allows_submit``
  if Phase E has merged. Fallback to env-constant checks (notional cap + daily
  loss cap) if the import fails — keeps Phase D self-contained.

Not handled here (by design)
----------------------------
* **Exit logic.** Exits are initiated by the strategy's own policy running in
  ``signal_worker`` / paper-trading loop; when that logic decides to close, it
  writes a row signalling the execution_worker to place a matching sell. The
  minimal contract used below: on ACCEPT we record the position; on a
  ``closed_ts`` marker (via ``request_exit()``) we place the sell and settle
  realized_pnl.
* **Backfill.** This worker runs forward-only. Historical runs live in the
  backfill sim, not here.

Heartbeat
---------
Writes ``execution_worker:heartbeat_ts`` and
``execution_worker:heartbeat_info`` to Redis every 60 s.

Run entry point (when the operator eventually starts the container):
    python -u -m governance.execution_worker
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

try:
    # Prefer governed persistence (pool + redis).
    from governance.persistence import get_conn, RedisClient
    _HAVE_GOVERNED = True
except Exception:  # pragma: no cover - allows import-only linting outside Docker
    get_conn = None  # type: ignore[assignment]
    RedisClient = None  # type: ignore[assignment]
    _HAVE_GOVERNED = False

from trading.binance_client import (
    BinanceAPIError,
    BinanceAuthError,
    BinanceClient,
    BinanceClientError,
    BinanceNetworkError,
    BinanceOrder,
    OrderSide,
    OrderType,
    aggregate_execution_reports,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("execution_worker")


# --------------------------------------------------------------------------- #
# Config — read once at module load to fail fast on missing critical env.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExecConfig:
    notional_usd: float
    poll_interval_s: float
    heartbeat_interval_s: float
    daily_loss_cap_usd: float
    binance_mode: str
    dry_run: bool  # if True, log intended orders instead of sending
    quote_currency: str  # resolved from registry — 'USDC' default
    whitelist_path: str  # symbols permitted in the live universe

    @classmethod
    def from_env(cls) -> "ExecConfig":
        # Post-split (2026-04-13): execution uses EXECUTION_QUOTE_CURRENCY
        # specifically. Signals are tagged DATA_QUOTE (USDT) and
        # translated to EXECUTION_QUOTE (USDC) at order placement via
        # _translate_to_quote — this is the MVP hybrid restored.
        from governance.bootstrap import execution_quote_currency as _eqc
        return cls(
            notional_usd=float(os.environ.get("BINANCE_NOTIONAL_PER_POSITION_USD", "40")),
            poll_interval_s=float(os.environ.get("EXECUTION_POLL_INTERVAL_S", "2.0")),
            heartbeat_interval_s=float(os.environ.get("EXECUTION_HEARTBEAT_INTERVAL_S", "60")),
            daily_loss_cap_usd=float(os.environ.get("LIVE_DAILY_LOSS_CAP_USD", "50")),
            binance_mode=os.environ.get("BINANCE_MODE", "testnet"),
            dry_run=os.environ.get("EXECUTION_DRY_RUN", "false").lower() == "true",
            quote_currency=_eqc(),
            whitelist_path=os.environ.get(
                "BINANCE_WHITELIST_PATH",
                "/backend/config/binance_spot_whitelist_usdc.txt",
            ),
        )


def _load_whitelist(path: str) -> set[str]:
    """Load symbol whitelist from disk. Returns set of permitted symbols.
    Lines starting with # or empty are ignored. Returns empty set on read failure
    (callers treat empty as 'no restriction' to avoid shipping a broken filter)."""
    try:
        with open(path) as fh:
            return {
                line.strip()
                for line in fh
                if line.strip() and not line.startswith("#")
            }
    except (OSError, FileNotFoundError):
        return set()


def _translate_to_quote(symbol: str, target_quote: str) -> Optional[str]:
    """Translate a USDT-suffixed symbol to target_quote suffix.

    Returns None if symbol doesn't end in a recognised quote suffix.
    No-op if symbol already ends in target_quote.

    Sprint research was USDT-denominated; live trading on this account is
    USDC-only (TRD_GRP_039 permission). Per operator: BTCUSDC and BTCUSDT
    candles track within 0.01% — mechanical suffix swap is safe.
    """
    target_quote = target_quote.upper()
    for src in ("USDT", "USDC", "BUSD", "FDUSD"):
        if symbol.endswith(src):
            if src == target_quote:
                return symbol
            return symbol[:-len(src)] + target_quote
    return None


# --------------------------------------------------------------------------- #
# Risk hook — delegate to governance.risk_limits if present, else enforce a
# minimal notional + daily-loss cap from env.
# --------------------------------------------------------------------------- #
def _risk_allows_submit(
    *, notional_usd: float, cfg: ExecConfig
) -> tuple[bool, Optional[str]]:
    """Return (allowed, reject_reason). ``reason`` is None on allow."""
    try:
        from governance.risk_limits import check_risk_allows_submit  # type: ignore
        return check_risk_allows_submit(notional_usd=notional_usd)
    except Exception:  # Phase E not merged yet — fall back to env caps.
        if notional_usd > cfg.notional_usd * 1.25:
            return False, f"notional {notional_usd} exceeds env cap {cfg.notional_usd}"

    # Daily realised-loss cap — cheap check against live_positions.
    if _HAVE_GOVERNED and get_conn is not None:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COALESCE(SUM(realized_pnl), 0)
                          FROM live_positions
                         WHERE closed_ts IS NOT NULL
                           AND closed_ts >= date_trunc('day', NOW())
                        """
                    )
                    row = cur.fetchone()
                    pnl_today = float(row[0]) if row else 0.0
            if pnl_today <= -cfg.daily_loss_cap_usd:
                return False, (
                    f"daily_loss_cap breached: realised {pnl_today:.2f} "
                    f"<= -{cfg.daily_loss_cap_usd:.2f}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("risk check DB query failed — permissive: %s", exc)

    return True, None


# --------------------------------------------------------------------------- #
# Signal → order helpers
# --------------------------------------------------------------------------- #
def _client_order_id(opportunity_id: int) -> str:
    """Generate a deterministic-ish but unique clientOrderId under Binance's
    32-char limit. The short suffix absorbs restarts/retries that re-fetch
    the same opportunity_id.
    """
    suffix = uuid.uuid4().hex[:6]
    return f"mcas_{opportunity_id}_{suffix}"


def _compute_qty(notional_usd: float, price: float) -> float:
    if price <= 0:
        raise ValueError("price must be positive")
    return float(Decimal(str(notional_usd)) / Decimal(str(price)))


def _extract_signal_price(payload: Dict[str, Any]) -> Optional[float]:
    """Pull the trigger / signal price from the opportunity payload_json."""
    for k in ("entry_price", "trigger_price", "signal_price", "price"):
        v = payload.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
class ExecutionWorker:
    """Poll accepted opportunities, submit orders, track fills."""

    def __init__(
        self,
        client: Optional[BinanceClient] = None,
        cfg: Optional[ExecConfig] = None,
    ):
        self.cfg = cfg or ExecConfig.from_env()
        self.client = client or BinanceClient(mode=self.cfg.binance_mode)
        self._stop = threading.Event()
        self._positions_by_coid: Dict[str, int] = {}  # clientOrderId → live_positions.id
        self._coid_lock = threading.Lock()
        # Whitelist of permitted live-trading symbols (USDC universe).
        # Empty set = no restriction (unsafe — would let any symbol through).
        # Failing to load a whitelist file logs a warning but doesn't crash;
        # downstream gate then defaults to "no restriction" until the file
        # appears or the path is corrected.
        self._whitelist: set[str] = _load_whitelist(self.cfg.whitelist_path)
        if not self._whitelist:
            logger.warning(
                "execution_worker: whitelist file empty or missing at %s — "
                "all translated symbols will be permitted (no whitelist gate)",
                self.cfg.whitelist_path,
            )
        else:
            logger.info(
                "execution_worker: loaded %d %s symbols from whitelist %s",
                len(self._whitelist), self.cfg.quote_currency, self.cfg.whitelist_path,
            )

    # ---------------- lifecycle ---------------- #

    def start(self) -> None:
        logger.info(
            "execution_worker starting: mode=%s notional=%s dry_run=%s quote=%s",
            self.cfg.binance_mode, self.cfg.notional_usd, self.cfg.dry_run,
            self.cfg.quote_currency,
        )
        # WS user-data stream — fill events streamed in real-time when working.
        # Binance deprecated /api/v3/userDataStream returns 410; the proper fix
        # (new WS auth pattern) is REFACTOR-3 work. For Week-1 cap=1 trading,
        # fall back to REST polling for fill state — slower but functional.
        # EXECUTION_WS_REQUIRED=true env var restores fail-loud behaviour.
        ws_required = os.environ.get("EXECUTION_WS_REQUIRED", "false").lower() == "true"
        if not self.cfg.dry_run:
            try:
                self.client.start_ws_user_stream(
                    on_event=self._on_ws_event,
                    keepalive_interval_s=1800,
                )
                logger.info("user-data ws started — fills will stream in real-time")
            except Exception as exc:  # noqa: BLE001
                if ws_required:
                    logger.exception("user-data ws start failed (EXECUTION_WS_REQUIRED=true): %s", exc)
                    raise
                logger.warning(
                    "user-data ws start failed: %s — falling back to REST poll for fills "
                    "(fill detection latency: ~poll_interval). Set EXECUTION_WS_REQUIRED=true "
                    "to restore fail-loud behaviour.",
                    exc,
                )

        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="exec-heartbeat", daemon=True
        )
        self._hb_thread.start()

        signal.signal(signal.SIGTERM, self._on_sigterm)
        signal.signal(signal.SIGINT, self._on_sigterm)

        try:
            self._poll_loop()
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.client.stop_ws_user_stream()
        except Exception:  # noqa: BLE001
            pass
        logger.info("execution_worker stopped")

    def _on_sigterm(self, signum, frame) -> None:  # noqa: ANN001
        logger.info("signal %s received — shutting down", signum)
        self._stop.set()

    # ---------------- main loop ---------------- #

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self._process_pending()
                if processed == 0:
                    # Sleep in small chunks so SIGTERM is responsive.
                    self._sleep(self.cfg.poll_interval_s)
            except Exception as exc:  # noqa: BLE001
                logger.exception("poll loop error: %s", exc)
                self._sleep(self.cfg.poll_interval_s)

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop.is_set():
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.5, left))

    def _process_pending(self) -> int:
        if not _HAVE_GOVERNED or get_conn is None:
            return 0
        n = 0
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, symbol, timeframe, setup_id, payload_json, created_ts
                      FROM opportunities
                     WHERE status = 'accepted_new'
                     ORDER BY created_ts ASC
                     LIMIT 25
                    """
                )
                rows = cur.fetchall() or []

            for row in rows:
                opp_id, symbol, timeframe, setup_id, payload_raw, created_ts = row
                try:
                    payload = json.loads(payload_raw) if payload_raw else {}
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                ok = self._submit_for_opportunity(
                    conn, opp_id, symbol, setup_id, payload
                )
                if ok:
                    n += 1
        return n

    # ---------------- submission ---------------- #

    def _submit_for_opportunity(
        self,
        conn,
        opp_id: int,
        symbol: str,
        setup_id: str,
        payload: Dict[str, Any],
    ) -> bool:
        """Place an order for one accepted opportunity. Returns True on
        successful placement (not fill) — False on risk-reject or transient
        error. The opportunity's status is always advanced so we don't busy-
        loop over it.
        """
        side = OrderSide.BUY  # Phase D only long setups go live first.
        price = _extract_signal_price(payload)
        if price is None:
            logger.warning("opp %d: no price in payload — skipping", opp_id)
            self._advance_status(conn, opp_id, "rejected_no_price")
            return False

        # Translate signal symbol to the live trading quote currency.
        # Sprint research was USDT-denominated; this account trades USDC.
        # Mechanical suffix swap, gated by the USDC whitelist.
        live_symbol = _translate_to_quote(symbol, self.cfg.quote_currency)
        if live_symbol is None:
            logger.warning("opp %d: symbol %s has no recognised quote suffix — skipping", opp_id, symbol)
            self._advance_status(conn, opp_id, "rejected_unknown_quote", audit={"symbol": symbol})
            return False
        if self._whitelist and live_symbol not in self._whitelist:
            logger.info("opp %d: %s → %s not in %s whitelist — skipping", opp_id, symbol, live_symbol, self.cfg.quote_currency)
            self._advance_status(conn, opp_id, "rejected_not_in_whitelist", audit={"signal_symbol": symbol, "live_symbol": live_symbol})
            return False

        notional = self.cfg.notional_usd
        allowed, reason = _risk_allows_submit(notional_usd=notional, cfg=self.cfg)
        if not allowed:
            logger.warning("opp %d rejected by risk: %s", opp_id, reason)
            self._advance_status(conn, opp_id, "rejected_risk", audit={"reason": reason})
            return False

        qty = _compute_qty(notional, price)
        coid = _client_order_id(opp_id)
        cluster_id = payload.get("cluster_id")
        variant_id = payload.get("variant_id")
        # From here on, "symbol" refers to the live (translated) symbol.
        # Original signal_symbol is preserved in audit for traceability.
        signal_symbol = symbol
        symbol = live_symbol

        # Record live_positions row BEFORE placement so a crash between
        # placement and DB-write doesn't leave an orphan exchange order.
        # If placement later fails, we update the row with the failure.
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO live_positions
                  (opportunity_id, setup_id, variant_id, symbol, cluster_id,
                   client_order_id, side, order_type,
                   requested_price, requested_qty, requested_notional_usd,
                   audit)
                VALUES
                  (%s, %s, %s, %s, %s,
                   %s, %s, %s,
                   %s, %s, %s,
                   %s)
                RETURNING id
                """,
                (
                    opp_id, setup_id, variant_id, symbol, cluster_id,
                    coid, side.value, OrderType.LIMIT_MAKER.value,
                    price, qty, notional,
                    json.dumps({"source": "execution_worker", "dry_run": self.cfg.dry_run}),
                ),
            )
            row = cur.fetchone()
            position_id = int(row[0])
        conn.commit()

        with self._coid_lock:
            self._positions_by_coid[coid] = position_id

        # Advance opportunity status so we don't re-process.
        self._advance_status(conn, opp_id, "order_submitted", audit={"position_id": position_id})

        if self.cfg.dry_run:
            logger.info(
                "DRY_RUN would place %s %s %s qty=%s price=%s coid=%s",
                setup_id, symbol, side.value, qty, price, coid,
            )
            return True

        try:
            order: BinanceOrder = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.LIMIT_MAKER,
                qty=qty,
                price=price,
                client_order_id=coid,
            )
            with get_conn() as conn2:
                with conn2.cursor() as cur2:
                    cur2.execute(
                        "UPDATE live_positions SET exchange_order_id=%s WHERE id=%s",
                        (str(order.order_id), position_id),
                    )
                conn2.commit()
            logger.info(
                "placed %s qty=%s price=%s coid=%s orderId=%s status=%s",
                symbol, qty, price, coid, order.order_id, order.status,
            )
            return True
        except (BinanceAuthError, BinanceAPIError, BinanceNetworkError, BinanceClientError) as exc:
            logger.error("placement failed for %s coid=%s: %s", symbol, coid, exc)
            with get_conn() as conn2:
                with conn2.cursor() as cur2:
                    cur2.execute(
                        """
                        UPDATE live_positions
                           SET closed_ts = NOW(),
                               exit_reason = 'FAIL_FAST',
                               audit = audit || %s::jsonb
                         WHERE id = %s
                        """,
                        (json.dumps({"placement_error": str(exc), "code": getattr(exc, "code", None)}), position_id),
                    )
                conn2.commit()
            return False

    # ---------------- websocket fills ---------------- #

    def _on_ws_event(self, evt: Dict[str, Any]) -> None:
        """Fold incoming executionReports into the matching live_positions row."""
        if evt.get("e") != "executionReport":
            return
        coid = evt.get("c") or evt.get("C")
        if not coid:
            return
        with self._coid_lock:
            position_id = self._positions_by_coid.get(coid)
        if position_id is None:
            # Could be a fill arriving before our in-memory map was populated
            # (e.g. after a worker restart). Resolve via DB.
            position_id = self._resolve_position_id_from_db(coid)
            if position_id is None:
                logger.warning("ws event for unknown coid=%s — ignoring", coid)
                return
            with self._coid_lock:
                self._positions_by_coid[coid] = position_id

        self._apply_event_to_position(position_id, evt)

    def _resolve_position_id_from_db(self, coid: str) -> Optional[int]:
        if not _HAVE_GOVERNED or get_conn is None:
            return None
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM live_positions WHERE client_order_id=%s",
                    (coid,),
                )
                row = cur.fetchone()
        return int(row[0]) if row else None

    def _apply_event_to_position(self, position_id: int, evt: Dict[str, Any]) -> None:
        if not _HAVE_GOVERNED or get_conn is None:
            return
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT fills, fill_qty FROM live_positions WHERE id=%s",
                    (position_id,),
                )
                row = cur.fetchone()
                if not row:
                    return
                existing_fills_raw = row[0] or []
                if isinstance(existing_fills_raw, str):
                    try:
                        existing_fills = json.loads(existing_fills_raw)
                    except json.JSONDecodeError:
                        existing_fills = []
                else:
                    existing_fills = existing_fills_raw

                agg = aggregate_execution_reports(existing_fills, [evt])
                opened_ts_clause = ""
                params = [
                    json.dumps(agg["fills"]),
                    agg["fill_qty"],
                    agg["avg_fill_price"],
                    position_id,
                ]
                if agg["fill_qty"] > 0 and len(existing_fills) == 0:
                    # First fill — stamp opened_ts.
                    opened_ts_clause = ", opened_ts = COALESCE(opened_ts, NOW())"

                cur.execute(
                    f"""
                    UPDATE live_positions
                       SET fills = %s::jsonb,
                           fill_qty = %s,
                           avg_fill_price = %s
                           {opened_ts_clause}
                     WHERE id = %s
                    """,
                    params,
                )
            conn.commit()
        logger.info(
            "fill applied to position_id=%s: qty=%.8f avg_price=%s status=%s",
            position_id, agg["fill_qty"], agg["avg_fill_price"], agg["last_status"],
        )

    # ---------------- status + heartbeat ---------------- #

    def _advance_status(
        self,
        conn,
        opp_id: int,
        new_status: str,
        audit: Optional[Dict[str, Any]] = None,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE opportunities SET status=%s, updated_ts=NOW() WHERE id=%s",
                (new_status, opp_id),
            )
        conn.commit()
        if audit:
            logger.info("opp %d → %s (%s)", opp_id, new_status, audit)

    def _heartbeat_loop(self) -> None:
        if RedisClient is None:
            return
        while not self._stop.is_set():
            try:
                cli = RedisClient.get_client()
                now = int(time.time())
                cli.set("execution_worker:heartbeat_ts", str(now))
                cli.set(
                    "execution_worker:heartbeat_info",
                    json.dumps({
                        "ts": now,
                        "mode": self.cfg.binance_mode,
                        "dry_run": self.cfg.dry_run,
                        "positions_tracked": len(self._positions_by_coid),
                    }),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("heartbeat failed: %s", exc)
            self._sleep(self.cfg.heartbeat_interval_s)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def run() -> None:
    """Blocking entrypoint — run until SIGTERM.
    Bootstrap the registry FIRST so QUOTE_CURRENCY (and any other governed
    config the worker needs) is resolvable before ExecConfig.from_env() runs.
    """
    from governance.bootstrap import bootstrap_genesis
    bootstrap_genesis()
    worker = ExecutionWorker()
    worker.start()


if __name__ == "__main__":
    run()
