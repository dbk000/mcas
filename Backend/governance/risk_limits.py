"""Risk limits — live trading control surface.

Phase E of Enforcer-First Deployment.

Provides:
    * :class:`RiskState` — dataclass snapshot of the live-trading envelope
      (notional per position, max daily loss, max concurrent, kill flag).
    * :func:`current_state` — reads Redis first (live operator overrides),
      falls back to environment variables, falls back to conservative
      defaults. The execution worker calls this on every order placement
      so no restart is required when the operator tweaks notional.
    * :func:`check_daily_loss_breach` — queries ``live_positions`` for
      today's realized_pnl and compares it to ``max_daily_loss_usd``.
      Caller is expected to invoke :func:`kill_all` on breach.
    * :func:`kill_all` — flips ``live_trading_enabled`` to 0, cancels
      queued runs, sets ``regime_state=SHOCK`` via the enforcer, and
      closes open paper positions (mirror of the existing emergency
      kill). Idempotent — calling it when already killed is a no-op
      beyond re-asserting the flag.

Redis keys read:
    ``BINANCE_NOTIONAL_PER_POSITION_USD`` — float, live-adjustable.
    ``BINANCE_MAX_DAILY_LOSS_USD``        — float, auto-scaled when
                                            notional changes.
    ``BINANCE_MAX_CONCURRENT``            — int.
    ``live_trading_enabled``              — "0"/"1" or "true"/"false".

Redis/env precedence: Redis value if present and parses → env var if
present and parses → hard-coded default.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults — matches the locked Day-1 values in the deploy plan.
# ---------------------------------------------------------------------------

DEFAULT_NOTIONAL_USD = 40.0
DEFAULT_MAX_DAILY_LOSS_USD = 120.0
DEFAULT_MAX_CONCURRENT = 1
DEFAULT_LIVE_TRADING_ENABLED = False

NOTIONAL_MIN = 1.0
NOTIONAL_MAX = 500.0  # Week-1 ceiling per plan §4.

REDIS_KEY_NOTIONAL = "BINANCE_NOTIONAL_PER_POSITION_USD"
REDIS_KEY_MAX_DAILY_LOSS = "BINANCE_MAX_DAILY_LOSS_USD"
REDIS_KEY_MAX_CONCURRENT = "BINANCE_MAX_CONCURRENT"
REDIS_KEY_LIVE_TRADING = "live_trading_enabled"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class RiskState:
    """Immutable snapshot of the live-trading risk envelope."""

    notional_per_position_usd: float
    max_daily_loss_usd: float
    max_concurrent: int
    live_trading_enabled: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _truthy(raw: Any) -> bool:
    """Parse Redis/env strings into bool. Anything other than 1/true/yes
    (case-insensitive) is False. Accepts bytes (Redis without
    decode_responses).
    """
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _read(
    r: Any,
    key: str,
    default: Any,
    cast: Callable[[Any], Any],
) -> Any:
    """Redis-first, env-fallback, default-last lookup.

    Parse failures at either Redis or env layer fall through to the
    next source rather than raising — a corrupt Redis value must not
    brick the execution path.
    """
    # Redis first.
    try:
        raw = r.get(key) if r is not None else None
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("risk_limits: Redis read failed for %s: %s", key, exc)
        raw = None
    if raw is not None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            return cast(raw)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "risk_limits: Redis value for %s failed cast (%r): %s",
                key,
                raw,
                exc,
            )
    # Env next.
    env_val = os.environ.get(key)
    if env_val is not None and env_val != "":
        try:
            return cast(env_val)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "risk_limits: env %s failed cast (%r): %s",
                key,
                env_val,
                exc,
            )
    return default


def current_state(r: Any) -> RiskState:
    """Read the current RiskState from Redis with env fallback.

    ``r`` is a redis-py client (or compatible: fakeredis works for
    tests). ``r`` may be ``None`` — the function still returns defaults
    via the env layer. This keeps unit tests simple.
    """
    notional = float(
        _read(r, REDIS_KEY_NOTIONAL, DEFAULT_NOTIONAL_USD, float)
    )
    max_daily_loss = float(
        _read(r, REDIS_KEY_MAX_DAILY_LOSS, DEFAULT_MAX_DAILY_LOSS_USD, float)
    )
    max_concurrent = int(
        _read(r, REDIS_KEY_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT, int)
    )
    live_enabled = bool(
        _read(
            r,
            REDIS_KEY_LIVE_TRADING,
            DEFAULT_LIVE_TRADING_ENABLED,
            _truthy,
        )
    )
    return RiskState(
        notional_per_position_usd=notional,
        max_daily_loss_usd=max_daily_loss,
        max_concurrent=max_concurrent,
        live_trading_enabled=live_enabled,
    )


# ---------------------------------------------------------------------------
# Setters — used by the HTTP router.
# ---------------------------------------------------------------------------


def set_notional(r: Any, notional_usd: float) -> Tuple[float, float]:
    """Set notional per position and auto-scale max_daily_loss = 3x.

    Bounds-checked against :data:`NOTIONAL_MIN` / :data:`NOTIONAL_MAX`.
    Returns the (new_notional, new_max_daily_loss) tuple.
    Raises ``ValueError`` on out-of-range input.
    """
    if not isinstance(notional_usd, (int, float)):
        raise ValueError(
            f"notional_usd must be numeric, got {type(notional_usd).__name__}"
        )
    if notional_usd < NOTIONAL_MIN or notional_usd > NOTIONAL_MAX:
        raise ValueError(
            f"notional_usd out of range [{NOTIONAL_MIN}, {NOTIONAL_MAX}]: "
            f"{notional_usd}"
        )
    new_notional = float(notional_usd)
    new_loss = new_notional * 3.0
    r.set(REDIS_KEY_NOTIONAL, str(new_notional))
    r.set(REDIS_KEY_MAX_DAILY_LOSS, str(new_loss))
    logger.info(
        "risk_limits: notional -> $%.2f, max_daily_loss -> $%.2f",
        new_notional,
        new_loss,
    )
    return new_notional, new_loss


def set_cap(r: Any, cap: int) -> int:
    """Set max concurrent positions. Returns the new value."""
    cap_int = int(cap)
    if cap_int < 1 or cap_int > 100:
        raise ValueError(f"cap out of range [1, 100]: {cap_int}")
    r.set(REDIS_KEY_MAX_CONCURRENT, str(cap_int))
    logger.info("risk_limits: max_concurrent -> %d", cap_int)
    return cap_int


def set_live_trading(r: Any, enabled: bool) -> bool:
    """Flip the ``live_trading_enabled`` flag. Returns the new value."""
    new_val = "1" if enabled else "0"
    r.set(REDIS_KEY_LIVE_TRADING, new_val)
    logger.info("risk_limits: live_trading_enabled -> %s", bool(enabled))
    return bool(enabled)


# ---------------------------------------------------------------------------
# Daily-loss breach detection.
# ---------------------------------------------------------------------------


def _today_start_utc() -> datetime:
    """Start-of-day in UTC. Day boundary is 00:00 UTC — matches the
    execution worker's accounting."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def today_pnl(pg_conn: Any) -> float:
    """Return today's realized PnL from ``live_positions`` (UTC day).

    Includes only positions closed today (``closed_ts >= start_of_day``).
    Open positions contribute nothing because ``realized_pnl`` is NULL
    until exit. NULLs are treated as 0.
    """
    if pg_conn is None:
        return 0.0
    start = _today_start_utc()
    sql = (
        "SELECT COALESCE(SUM(realized_pnl), 0)::float "
        "FROM live_positions "
        "WHERE closed_ts IS NOT NULL AND closed_ts >= %s"
    )
    try:
        with pg_conn.cursor() as cur:
            cur.execute(sql, (start,))
            row = cur.fetchone()
    except Exception as exc:
        logger.warning("risk_limits: today_pnl query failed: %s", exc)
        return 0.0
    if not row:
        return 0.0
    val = row[0]
    return float(val) if val is not None else 0.0


def check_daily_loss_breach(
    pg_conn: Any,
    state: RiskState,
) -> Tuple[bool, float]:
    """Return ``(breached, today_pnl)``.

    ``breached`` is True iff ``today_pnl <= -max_daily_loss_usd``.
    Uses a strict inequality flipped — "at or below negative limit" is
    a breach. Caller auto-kills live_trading + sets SHOCK.
    """
    pnl = today_pnl(pg_conn)
    breached = pnl <= -abs(state.max_daily_loss_usd)
    return breached, pnl


# ---------------------------------------------------------------------------
# Kill all.
# ---------------------------------------------------------------------------


def kill_all(
    pg_conn: Any,
    redis_client: Any,
    reason: str = "manual_kill_all",
    enforcer: Any = None,
) -> dict:
    """Flip live_trading off, cancel queued runs, set SHOCK, close paper positions.

    This is the "red button" — idempotent, best-effort across each
    side-effect. Returns a summary dict:

        {
          'live_trading_enabled': False,
          'cancelled_runs': int,
          'closed_positions': int,  # paper
          'regime_state_changed': bool,
          'reason': str,
        }

    ``enforcer`` — optional :class:`governance.regime.enforcer.Enforcer`
    instance. If provided, its ``set_state`` is used (so the audit row
    is written). If absent, we fall back to a raw Redis write to the
    ``regime_state`` key — still correct but no audit row.
    """
    summary = {
        "live_trading_enabled": False,
        "cancelled_runs": 0,
        "closed_positions": 0,
        "regime_state_changed": False,
        "reason": reason,
    }

    # 1. Flip the live flag first — stop the bleeding.
    try:
        set_live_trading(redis_client, False)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("kill_all: set_live_trading failed: %s", exc)

    # 2. Regime -> SHOCK.
    try:
        if enforcer is not None:
            from governance.regime.enforcer import RegimeState  # local import

            changed = enforcer.set_state(
                RegimeState.SHOCK,
                reason=reason,
                source="http_override",
                pg_conn=pg_conn,
            )
            summary["regime_state_changed"] = bool(changed)
        else:
            redis_client.set("regime_state", "SHOCK")
            summary["regime_state_changed"] = True
    except Exception as exc:
        logger.exception("kill_all: regime SHOCK flip failed: %s", exc)

    # 3. Cancel queued runs + close open paper positions.
    if pg_conn is not None:
        now = datetime.now(timezone.utc)
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE run_queue
                       SET status = 'FAILED',
                           completed_at = %s,
                           last_error = %s
                     WHERE status IN ('QUEUED', 'CLAIMED')
                    """,
                    (now, f"KILL_ALL: {reason}"),
                )
                summary["cancelled_runs"] = cur.rowcount or 0

                cur.execute(
                    """
                    UPDATE paper_positions
                       SET status = 'CLOSED',
                           exit_reason = 'EMERGENCY_KILL',
                           exit_ts = %s,
                           exit_price = COALESCE(current_price, entry_price),
                           updated_at = %s
                     WHERE status = 'OPEN'
                    """,
                    (now, now),
                )
                summary["closed_positions"] = cur.rowcount or 0
            if getattr(pg_conn, "autocommit", False) is False:
                pg_conn.commit()
        except Exception as exc:
            logger.exception("kill_all: DB writes failed: %s", exc)

    # 4. Publish an emergency_control event for live listeners (mirror of
    #    emergency_kill contract so existing subscribers react correctly).
    try:
        import json as _json

        redis_client.publish(
            "emergency_control",
            _json.dumps(
                {
                    "action": "kill_all",
                    "reason": reason,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
        )
    except Exception:
        pass  # best-effort

    return summary


__all__ = [
    "DEFAULT_LIVE_TRADING_ENABLED",
    "DEFAULT_MAX_CONCURRENT",
    "DEFAULT_MAX_DAILY_LOSS_USD",
    "DEFAULT_NOTIONAL_USD",
    "NOTIONAL_MAX",
    "NOTIONAL_MIN",
    "REDIS_KEY_LIVE_TRADING",
    "REDIS_KEY_MAX_CONCURRENT",
    "REDIS_KEY_MAX_DAILY_LOSS",
    "REDIS_KEY_NOTIONAL",
    "RiskState",
    "check_daily_loss_breach",
    "current_state",
    "kill_all",
    "set_cap",
    "set_live_trading",
    "set_notional",
    "today_pnl",
]
