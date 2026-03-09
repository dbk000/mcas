"""
Equities Simulation Runner
===========================

POST /v1/runs/equities-simulation

Simulates expired equities opportunities using fixed-horizon exit model.
For each expired EQ_* opportunity, fetches daily OHLCV at horizon close
and computes PnL with round-trip cost.

Unlike crypto simulation (stop/TP exits walking through bars),
equities strategies use a simple hold-to-horizon model.
"""

import os
import re
import json
import time
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from db_pool import get_conn as _pool_get_conn

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

router = APIRouter(prefix="/runs", tags=["equities-simulation"])

_r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

EQ_SETUP_IDS = ["EQ_TSMOM", "EQ_XSMOM", "EQ_REV1A", "EQ_REV1B", "EQ_VPD", "EQ_ATC", "EQ_NVDA_MACRO"]

# Round-trip cost: 1.5 bps each side (equities commission via IBKR-style)
DEFAULT_COST_BPS = 3.0


def _get_conn():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return _pool_get_conn()


def _envelope(data, t0=None, **extra):
    meta = {"timestamp": time.time(), "version": "1.0.0"}
    if t0:
        meta["compute_ms"] = round((time.time() - t0) * 1000, 1)
    meta.update(extra)
    return {"data": data, "meta": meta}


# ── Request model ──────────────────────────────────────────────────────────

class EquitiesSimulationRequest(BaseModel):
    """Request to simulate expired equities opportunities."""
    name: Optional[str] = None
    setup_ids: Optional[list[str]] = None  # Filter by EQ_TSMOM, EQ_REV1B, etc.
    cost_bps: float = Field(default=DEFAULT_COST_BPS, ge=0, le=50)
    from_date: Optional[str] = None   # ISO YYYY-MM-DD inclusive
    to_date: Optional[str] = None     # ISO YYYY-MM-DD inclusive
    include_dates: Optional[list[str]] = None
    exclude_dates: Optional[list[str]] = None
    limit: int = Field(default=5000, ge=1, le=50000)

    @model_validator(mode="after")
    def validate_filters(self):
        _iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")

        def _check(val, field):
            if not _iso_re.match(val):
                raise ValueError(f"{field} must be YYYY-MM-DD, got '{val}'")

        if self.include_dates is not None and len(self.include_dates) == 0:
            self.include_dates = None
        if self.exclude_dates is not None and len(self.exclude_dates) == 0:
            self.exclude_dates = None

        for lst, name in [(self.include_dates, "include_dates"),
                          (self.exclude_dates, "exclude_dates")]:
            if lst:
                for d in lst:
                    _check(d, name)

        if self.from_date:
            _check(self.from_date, "from_date")
        if self.to_date:
            _check(self.to_date, "to_date")

        # Validate setup_ids are equities only
        if self.setup_ids:
            for sid in self.setup_ids:
                if not sid.startswith("EQ_"):
                    raise ValueError(f"Equities simulation only accepts EQ_* setup_ids, got '{sid}'")
        return self


# ── Simulation logic ───────────────────────────────────────────────────────

def _simulate_opportunity(cur, opp, cost_bps: float) -> dict:
    """
    Simulate a single equities opportunity.

    Returns a dict ready for INSERT into simulation_results.
    """
    opp_id = opp["id"]
    symbol = opp["symbol"]
    setup_id = opp["setup_id"]
    created_ts = opp["created_ts"]

    payload = opp["payload"] if isinstance(opp["payload"], dict) else json.loads(opp["payload"])
    direction = payload.get("direction", "LONG")
    horizon_days = payload.get("horizon_days", 20)
    entry_price = payload.get("entry_price")

    if not entry_price or entry_price <= 0:
        return {
            "opportunity_id": opp_id,
            "symbol": symbol,
            "timeframe": "1d",
            "setup_id": setup_id,
            "direction": direction,
            "opportunity_created_ts": created_ts,
            "data_available": False,
            "error_message": "Missing or invalid entry_price in payload",
        }

    # Fetch daily closes after the opportunity was created
    cur.execute("""
        SELECT bar_start_ts, close
        FROM ohlcv
        WHERE symbol = %s
          AND timeframe = '1d'
          AND source = 'equities_yahoo'
          AND bar_start_ts > %s
        ORDER BY bar_start_ts ASC
        LIMIT %s
    """, (symbol, created_ts, horizon_days))

    bars = cur.fetchall()

    if not bars:
        return {
            "opportunity_id": opp_id,
            "symbol": symbol,
            "timeframe": "1d",
            "setup_id": setup_id,
            "direction": direction,
            "opportunity_created_ts": created_ts,
            "data_available": False,
            "error_message": f"No OHLCV bars found after {created_ts}",
        }

    # Exit at the last available bar up to horizon
    exit_bar = bars[-1]
    exit_ts = exit_bar[0]
    exit_price = float(exit_bar[1])
    bars_held = len(bars)

    # PnL computation
    dir_mult = 1.0 if direction == "LONG" else -1.0
    pnl_gross_pct = dir_mult * (exit_price / entry_price - 1.0)
    cost_pct = cost_bps / 10000.0
    pnl_net_pct = pnl_gross_pct - cost_pct

    # Position-size-agnostic: store as fractional returns
    # For aggregation, the API consumer multiplies by position_size
    pnl_gross = round(pnl_gross_pct, 8)
    fees = round(cost_pct, 8)
    pnl_net = round(pnl_net_pct, 8)

    return {
        "opportunity_id": opp_id,
        "symbol": symbol,
        "timeframe": "1d",
        "setup_id": setup_id,
        "direction": direction,
        "opportunity_created_ts": created_ts,
        "entry_ts": created_ts,
        "entry_price": entry_price,
        "entry_price_with_slippage": entry_price,  # No slippage model for daily equities
        "position_size": 1.0,  # Fractional return mode
        "stop_price": None,
        "tp1_price": None,
        "tp2_price": None,
        "r_value": None,
        "exit_ts": exit_ts,
        "exit_price": exit_price,
        "exit_price_with_slippage": exit_price,
        "exit_reason": "HORIZON_EXIT",
        "bars_held": bars_held,
        "pnl_gross": pnl_gross,
        "fees": fees,
        "pnl_net": pnl_net,
        "r_multiple": None,  # Not applicable to fixed-horizon
        "data_available": True,
        "error_message": None,
    }


# ── Endpoint ───────────────────────────────────────────────────────────────

@router.post("/equities-simulation")
def create_equities_simulation(request: EquitiesSimulationRequest):
    """
    Simulate expired equities opportunities using fixed-horizon exit model.

    Unlike crypto simulation which queues to the worker, this runs inline
    because equities simulation is fast (~5ms per opportunity on daily bars).
    """
    t0 = time.time()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Build WHERE clause for opportunity selection
    setup_ids = request.setup_ids or EQ_SETUP_IDS
    conditions = [
        "status = 'expired'",
        "setup_id = ANY(%s)",
    ]
    params: list = [setup_ids]

    if request.include_dates:
        conditions.append("created_ts::date = ANY(%s::date[])")
        params.append(request.include_dates)
    else:
        if request.from_date:
            conditions.append("created_ts >= %s::date")
            params.append(request.from_date)
        if request.to_date:
            conditions.append("created_ts < (%s::date + INTERVAL '1 day')")
            params.append(request.to_date)

    if request.exclude_dates:
        conditions.append("NOT (created_ts::date = ANY(%s::date[]))")
        params.append(request.exclude_dates)

    where_clause = " AND ".join(conditions)

    with _get_conn() as conn:
        with conn.cursor() as cur:
            # Fetch expired equities opportunities
            cur.execute(f"""
                SELECT id, symbol, setup_id, created_ts, payload_json
                FROM opportunities
                WHERE {where_clause}
                ORDER BY created_ts ASC
                LIMIT %s
            """, params + [request.limit])

            rows = cur.fetchall()

            if not rows:
                raise HTTPException(
                    status_code=400,
                    detail="No expired equities opportunities found matching criteria"
                )

            opportunities = []
            for row in rows:
                payload_raw = row[4]
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                opportunities.append({
                    "id": row[0],
                    "symbol": row[1],
                    "setup_id": row[2],
                    "created_ts": row[3],
                    "payload": payload,
                })

            # Simulate each opportunity
            results = []
            for opp in opportunities:
                result = _simulate_opportunity(cur, opp, request.cost_bps)
                results.append(result)

            # Create run record
            strategy_json = json.dumps({
                "setup_id": ",".join(setup_ids),
                "setup_params": {
                    "setup_ids": setup_ids,
                    "cost_bps": request.cost_bps,
                    "from_date": request.from_date,
                    "to_date": request.to_date,
                    "limit": request.limit,
                },
            })

            execution_json = json.dumps({
                "entry_mode": "MARKET_CLOSE",
                "exit_mode": "HORIZON",
                "cost_bps": request.cost_bps,
            })

            data_json = json.dumps({
                "data_source": "equities_simulation",
                "data_snapshot_ts": now.isoformat(),
            })

            fp_input = f"{strategy_json}{execution_json}{run_id}"
            fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:16]
            name = request.name or f"eq_sim_{now.strftime('%Y%m%d_%H%M%S')}"

            cur.execute("""
                INSERT INTO runs (
                    id, run_type, status, name, fingerprint,
                    engine_version, universe_json, strategy_json,
                    execution_json, data_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                run_id, "SIMULATION", "COMPLETED", name, fingerprint,
                "1.0.0", json.dumps({"venue": "equities_yahoo"}),
                strategy_json, execution_json, data_json, now,
            ))

            # Batch insert simulation results
            if results:
                cols = [
                    "run_id", "opportunity_id", "symbol", "timeframe",
                    "setup_id", "direction", "opportunity_created_ts",
                    "entry_ts", "entry_price", "entry_price_with_slippage",
                    "position_size", "stop_price", "tp1_price", "tp2_price",
                    "r_value", "exit_ts", "exit_price", "exit_price_with_slippage",
                    "exit_reason", "bars_held", "pnl_gross", "fees", "pnl_net",
                    "r_multiple", "data_available", "error_message",
                ]

                values_template = ", ".join(["%s"] * len(cols))
                insert_sql = f"""
                    INSERT INTO simulation_results ({', '.join(cols)})
                    VALUES ({values_template})
                """

                batch = []
                for r_item in results:
                    row_vals = [run_id]
                    for col in cols[1:]:  # skip run_id, already added
                        row_vals.append(r_item.get(col))
                    batch.append(row_vals)

                # Write in batches of 500
                batch_size = 500
                for i in range(0, len(batch), batch_size):
                    chunk = batch[i:i + batch_size]
                    cur.executemany(insert_sql, chunk)

            # Mark run finished
            cur.execute("""
                UPDATE runs SET status = 'COMPLETED',
                    finished_at = NOW()
                WHERE id = %s
            """, (run_id,))

        conn.commit()

    # Summary stats
    data_available = [r for r in results if r.get("data_available")]
    no_data = len(results) - len(data_available)
    winners = [r for r in data_available if (r.get("pnl_net") or 0) > 0]
    losers = [r for r in data_available if (r.get("pnl_net") or 0) <= 0]
    total_pnl = sum(r.get("pnl_net", 0) for r in data_available)

    # Per-strategy breakdown
    by_strategy = {}
    for r_item in data_available:
        sid = r_item["setup_id"]
        if sid not in by_strategy:
            by_strategy[sid] = {"count": 0, "wins": 0, "pnl_sum": 0.0}
        by_strategy[sid]["count"] += 1
        by_strategy[sid]["pnl_sum"] += r_item.get("pnl_net", 0)
        if (r_item.get("pnl_net") or 0) > 0:
            by_strategy[sid]["wins"] += 1

    strategy_summary = {}
    for sid, stats in by_strategy.items():
        strategy_summary[sid] = {
            "trades": stats["count"],
            "wins": stats["wins"],
            "win_rate": round(stats["wins"] / max(stats["count"], 1), 3),
            "total_pnl_pct": round(stats["pnl_sum"] * 100, 4),
        }

    return _envelope({
        "run_id": run_id,
        "status": "COMPLETED",
        "name": name,
        "total_opportunities": len(results),
        "data_available": len(data_available),
        "no_data": no_data,
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / max(len(data_available), 1), 3),
        "total_pnl_pct": round(total_pnl * 100, 4),
        "avg_pnl_pct": round(total_pnl / max(len(data_available), 1) * 100, 4),
        "cost_bps": request.cost_bps,
        "by_strategy": strategy_summary,
        "message": f"Simulated {len(data_available)} equities opportunities ({no_data} missing data)",
    }, t0)
