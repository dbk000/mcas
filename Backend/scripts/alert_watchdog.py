#!/usr/bin/env python3
"""
Governed alert watchdog. Reads thresholds from TypedConfigRegistry.
Runs from cron every 6h. Checks signal staleness and alerts via webhook.

Cron setup:
  0 */6 * * * cd /srv/mcas/Backend && docker compose -f docker-compose.yml \
    run --rm -T worker python3 /scripts/alert_watchdog.py \
    >> /var/log/mcas_watchdog.log 2>&1
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution: Docker mounts PYTHONPATH=/backend; local dev needs manual.
# ---------------------------------------------------------------------------
if os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
else:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from governance.bootstrap import bootstrap_genesis, get_config  # noqa: E402

# ---------------------------------------------------------------------------
# Bootstrap — full governance compliance. Process dies if registry missing.
# ---------------------------------------------------------------------------
bootstrap_genesis()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("alert_watchdog")

# ---------------------------------------------------------------------------
# Read ALL config from frozen registry — crash if missing
# ---------------------------------------------------------------------------
alert_cfg = get_config().Alert
STALE_HOURS: int = alert_cfg.SIGNAL_STALE_HOURS
WEBHOOK_URL: str = alert_cfg.WEBHOOK_URL

if not WEBHOOK_URL:
    print(
        "FATAL: ALERT_WEBHOOK_URL is empty — cannot run watchdog without a destination",
        file=sys.stderr,
    )
    os._exit(78)

worker_cfg = get_config().Worker
DATABASE_URL: str = worker_cfg.DATABASE_URL

if not DATABASE_URL:
    print("FATAL: DATABASE_URL is empty — cannot connect to Postgres", file=sys.stderr)
    os._exit(78)


# ---------------------------------------------------------------------------
# Check 1: Signal staleness
# ---------------------------------------------------------------------------
def check_signal_staleness() -> tuple[float | None, datetime | None]:
    """Return (age_hours, last_signal_ts) from the opportunities table.

    Uses a direct psycopg connection rather than the pool — the watchdog is
    a short-lived script, not a long-running worker.  The DATABASE_URL is
    still governed (pulled from frozen WorkerTypedConfig).
    """
    import psycopg

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(created_ts) FROM opportunities WHERE batch_id IS NULL;"
            )
            row = cur.fetchone()
            if row is None or row[0] is None:
                return None, None
            last_ts = row[0]
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - last_ts
            return age.total_seconds() / 3600.0, last_ts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    now_utc = datetime.now(timezone.utc)
    LOG.info("Watchdog run at %s", now_utc.isoformat())

    signal_age_hours, last_signal_ts = check_signal_staleness()

    alerts: list[str] = []

    if signal_age_hours is None:
        alerts.append("Signals stale: NEVER received (no live opportunities in DB)")
    elif signal_age_hours > STALE_HOURS:
        alerts.append(
            f"Signals stale: {signal_age_hours:.1f}h (threshold: {STALE_HOURS}h)"
        )
        alerts.append(
            f"Last signal: {last_signal_ts.isoformat() if last_signal_ts else 'NEVER'}"
        )

    # -------------------------------------------------------------------
    # All clear?
    # -------------------------------------------------------------------
    if not alerts:
        msg = f"[OK] All checks passed. Last signal: {signal_age_hours:.1f}h ago"
        LOG.info(msg)
        print(msg)
        sys.exit(0)

    # -------------------------------------------------------------------
    # Build and send alert
    # -------------------------------------------------------------------
    alert_body = "\n".join(f"\u2022 {a}" for a in alerts)
    payload_text = f"\U0001f6a8 MCAS ALERT\n{alert_body}"

    LOG.warning("ALERT firing: %s", payload_text)

    payload = json.dumps({"content": payload_text})
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            LOG.info("Webhook sent: HTTP %s", resp.status)
    except Exception as exc:
        LOG.error("Webhook delivery failed: %s", exc)
        # Still write to persistent log even if webhook fails
        print(f"ERROR: Webhook delivery failed: {exc}", file=sys.stderr)

    # -------------------------------------------------------------------
    # Persistent log
    # -------------------------------------------------------------------
    output_dir = Path("/scripts/output")
    if not output_dir.is_dir():
        # Local dev fallback
        output_dir = Path(os.path.dirname(__file__)) / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    log_file = output_dir / f"watchdog_{now_utc.strftime('%Y%m%d_%H%M%S')}.log"
    log_file.write_text(
        f"timestamp: {now_utc.isoformat()}\n"
        f"stale_hours_threshold: {STALE_HOURS}\n"
        f"signal_age_hours: {signal_age_hours}\n"
        f"last_signal_ts: {last_signal_ts}\n"
        f"alerts:\n" + "\n".join(f"  - {a}" for a in alerts) + "\n",
        encoding="utf-8",
    )
    LOG.info("Log written to %s", log_file)


if __name__ == "__main__":
    main()
