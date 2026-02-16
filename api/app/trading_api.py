"""
Trading API
===========

Endpoints for paper trading data:
- Account status and balance
- Open positions
- Trade history
- Performance metrics

These endpoints allow the frontend to display trading state and outcomes.
"""

import os
import json
from datetime import datetime, timezone, timedelta

import psycopg
from fastapi import APIRouter, Query, HTTPException

DATABASE_URL = os.getenv("DATABASE_URL")

router = APIRouter(prefix="/trading", tags=["trading"])


def get_conn():
    """Get database connection."""
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    return psycopg.connect(DATABASE_URL)


# =============================================================================
# Account Endpoints
# =============================================================================

@router.get("/account")
def get_account():
    """
    Get the active paper trading account status.
    
    Returns:
        Current capital, P&L, win rate, drawdown, etc.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get active paper run
            cur.execute("""
                SELECT r.id, r.name, r.started_at, r.status,
                       pa.initial_capital, pa.current_capital, pa.peak_capital,
                       pa.total_trades, pa.winning_trades, pa.losing_trades,
                       pa.total_pnl, pa.total_fees, pa.max_drawdown,
                       pa.current_exposure, pa.daily_pnl
                FROM runs r
                JOIN paper_accounts pa ON r.id = pa.run_id
                WHERE r.run_type = 'PAPER' AND r.status = 'RUNNING'
                ORDER BY r.started_at DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
    
    if not row:
        return {
            "active": False,
            "message": "No active paper trading session",
        }
    
    (run_id, name, started_at, status,
     initial, current, peak,
     total_trades, wins, losses,
     total_pnl, fees, max_dd,
     exposure, daily_pnl) = row
    
    return {
        "active": True,
        "run_id": str(run_id),
        "run_name": name,
        "started_at": started_at.isoformat() if started_at else None,
        "status": status,
        "account": {
            "initial_capital": initial,
            "current_capital": current,
            "peak_capital": peak,
            "total_return_pct": ((current - initial) / initial * 100) if initial else 0,
            "total_pnl": total_pnl,
            "total_fees": fees,
            "daily_pnl": daily_pnl,
            "current_exposure": exposure,
            "available_capital": current - exposure,
            "max_drawdown_pct": max_dd * 100 if max_dd else 0,
        },
        "performance": {
            "total_trades": total_trades,
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": wins / max(1, total_trades),
            "profit_factor": _calculate_profit_factor(str(run_id)),
            "average_r": _calculate_average_r(str(run_id)),
        },
    }


def _calculate_profit_factor(run_id: str) -> float:
    """Calculate profit factor from closed positions."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) as gross_profit,
                        ABS(SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END)) as gross_loss
                    FROM paper_positions
                    WHERE run_id = %s AND status = 'CLOSED';
                """, (run_id,))
                row = cur.fetchone()
                if row and row[1] and row[1] > 0:
                    return row[0] / row[1]
    except Exception:
        pass
    return 0.0


def _calculate_average_r(run_id: str) -> float:
    """Calculate average R-multiple from closed positions."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT AVG(r_multiple)
                    FROM paper_positions
                    WHERE run_id = %s AND status = 'CLOSED';
                """, (run_id,))
                row = cur.fetchone()
                if row and row[0]:
                    return float(row[0])
    except Exception:
        pass
    return 0.0


# =============================================================================
# Position Endpoints
# =============================================================================

@router.get("/positions")
def get_positions(
    status: str = Query(default="OPEN", description="Position status: OPEN, CLOSED, or ALL"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Get positions for the active paper trading session.
    
    Args:
        status: Filter by position status
        limit: Maximum number of positions to return
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get active run
            cur.execute("""
                SELECT id FROM runs
                WHERE run_type = 'PAPER' AND status = 'RUNNING'
                ORDER BY started_at DESC
                LIMIT 1;
            """)
            run_row = cur.fetchone()
            
            if not run_row:
                return {"positions": [], "message": "No active paper trading session"}
            
            run_id = run_row[0]
            
            # Build query based on status filter
            if status.upper() == "ALL":
                status_filter = ""
            else:
                status_filter = f"AND pp.status = '{status.upper()}'"
            
            cur.execute(f"""
                SELECT pp.id, pp.symbol, pp.timeframe, pp.side, pp.setup_id,
                       pp.status, pp.signal_ts, pp.entry_ts,
                       pp.entry_price, pp.entry_price_with_slippage,
                       pp.stop_price, pp.tp1_price, pp.tp2_price,
                       pp.r_value, pp.position_size, pp.notional_value,
                       pp.risk_amount, pp.current_price, pp.unrealized_pnl,
                       pp.high_water_mark, pp.bars_held,
                       pp.exit_ts, pp.exit_price, pp.exit_reason,
                       pp.realized_pnl, pp.fees_paid, pp.r_multiple
                FROM paper_positions pp
                WHERE pp.run_id = %s {status_filter}
                ORDER BY pp.created_at DESC
                LIMIT %s;
            """, (run_id, limit))
            rows = cur.fetchall()
    
    positions = []
    for row in rows:
        positions.append({
            "id": str(row[0]),
            "symbol": row[1],
            "timeframe": row[2],
            "side": row[3],
            "setup_id": row[4],
            "status": row[5],
            "signal_ts": row[6].isoformat() if row[6] else None,
            "entry_ts": row[7].isoformat() if row[7] else None,
            "entry_price": row[8],
            "entry_price_with_slippage": row[9],
            "stop_price": row[10],
            "tp1_price": row[11],
            "tp2_price": row[12],
            "r_value": row[13],
            "position_size": row[14],
            "notional_value": row[15],
            "risk_amount": row[16],
            "current_price": row[17],
            "unrealized_pnl": row[18],
            "high_water_mark": row[19],
            "bars_held": row[20],
            "exit_ts": row[21].isoformat() if row[21] else None,
            "exit_price": row[22],
            "exit_reason": row[23],
            "realized_pnl": row[24],
            "fees_paid": row[25],
            "r_multiple": row[26],
        })
    
    return {
        "run_id": str(run_id),
        "status_filter": status,
        "count": len(positions),
        "positions": positions,
    }


@router.get("/positions/{position_id}")
def get_position(position_id: str):
    """Get details for a specific position."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pp.id, pp.run_id, pp.symbol, pp.timeframe, pp.side, pp.setup_id,
                       pp.status, pp.signal_ts, pp.entry_ts,
                       pp.entry_price, pp.entry_price_with_slippage,
                       pp.stop_price, pp.tp1_price, pp.tp2_price,
                       pp.r_value, pp.position_size, pp.notional_value,
                       pp.risk_amount, pp.current_price, pp.unrealized_pnl,
                       pp.high_water_mark, pp.bars_held,
                       pp.exit_ts, pp.exit_price, pp.exit_price_with_slippage,
                       pp.exit_reason, pp.realized_pnl, pp.fees_paid, pp.r_multiple,
                       pp.trace_json, pp.created_at, pp.updated_at
                FROM paper_positions pp
                WHERE pp.id = %s;
            """, (position_id,))
            row = cur.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Position not found")
    
    trace = json.loads(row[29]) if row[29] else {}
    
    return {
        "id": str(row[0]),
        "run_id": str(row[1]),
        "symbol": row[2],
        "timeframe": row[3],
        "side": row[4],
        "setup_id": row[5],
        "status": row[6],
        "signal_ts": row[7].isoformat() if row[7] else None,
        "entry_ts": row[8].isoformat() if row[8] else None,
        "entry_price": row[9],
        "entry_price_with_slippage": row[10],
        "stop_price": row[11],
        "tp1_price": row[12],
        "tp2_price": row[13],
        "r_value": row[14],
        "position_size": row[15],
        "notional_value": row[16],
        "risk_amount": row[17],
        "current_price": row[18],
        "unrealized_pnl": row[19],
        "high_water_mark": row[20],
        "bars_held": row[21],
        "exit_ts": row[22].isoformat() if row[22] else None,
        "exit_price": row[23],
        "exit_price_with_slippage": row[24],
        "exit_reason": row[25],
        "realized_pnl": row[26],
        "fees_paid": row[27],
        "r_multiple": row[28],
        "trace": trace,
        "created_at": row[30].isoformat() if row[30] else None,
        "updated_at": row[31].isoformat() if row[31] else None,
    }


# =============================================================================
# Trade History Endpoints
# =============================================================================

@router.get("/trades")
def get_trades(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    symbol: str = Query(default=None, description="Filter by symbol"),
    setup_id: str = Query(default=None, description="Filter by setup"),
):
    """
    Get trade history (closed positions) for the active session.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get active or most recent run
            cur.execute("""
                SELECT id FROM runs
                WHERE run_type = 'PAPER'
                ORDER BY started_at DESC
                LIMIT 1;
            """)
            run_row = cur.fetchone()
            
            if not run_row:
                return {"trades": [], "message": "No paper trading sessions found"}
            
            run_id = run_row[0]
            
            # Build filters
            filters = ["pp.run_id = %s", "pp.status = 'CLOSED'"]
            params = [run_id]
            
            if symbol:
                filters.append("pp.symbol = %s")
                params.append(symbol)
            if setup_id:
                filters.append("pp.setup_id = %s")
                params.append(setup_id)
            
            where_clause = " AND ".join(filters)
            
            cur.execute(f"""
                SELECT pp.id, pp.symbol, pp.side, pp.setup_id,
                       pp.entry_ts, pp.entry_price_with_slippage,
                       pp.exit_ts, pp.exit_price_with_slippage,
                       pp.exit_reason, pp.realized_pnl, pp.fees_paid,
                       pp.r_multiple, pp.bars_held
                FROM paper_positions pp
                WHERE {where_clause}
                ORDER BY pp.exit_ts DESC
                LIMIT %s OFFSET %s;
            """, params + [limit, offset])
            rows = cur.fetchall()
            
            # Get total count
            cur.execute(f"""
                SELECT COUNT(*) FROM paper_positions pp
                WHERE {where_clause};
            """, params)
            total = cur.fetchone()[0]
    
    trades = []
    for row in rows:
        trades.append({
            "id": str(row[0]),
            "symbol": row[1],
            "side": row[2],
            "setup_id": row[3],
            "entry_ts": row[4].isoformat() if row[4] else None,
            "entry_price": row[5],
            "exit_ts": row[6].isoformat() if row[6] else None,
            "exit_price": row[7],
            "exit_reason": row[8],
            "realized_pnl": row[9],
            "fees": row[10],
            "r_multiple": row[11],
            "bars_held": row[12],
        })
    
    return {
        "run_id": str(run_id),
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(trades),
        "trades": trades,
    }


# =============================================================================
# Performance Analytics
# =============================================================================

@router.get("/performance")
def get_performance():
    """
    Get detailed performance analytics for the active session.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get active run
            cur.execute("""
                SELECT id, started_at FROM runs
                WHERE run_type = 'PAPER' AND status = 'RUNNING'
                ORDER BY started_at DESC
                LIMIT 1;
            """)
            run_row = cur.fetchone()
            
            if not run_row:
                return {"error": "No active paper trading session"}
            
            run_id, started_at = run_row
            
            # Overall stats
            cur.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losers,
                    SUM(realized_pnl) as total_pnl,
                    AVG(r_multiple) as avg_r,
                    MAX(r_multiple) as best_r,
                    MIN(r_multiple) as worst_r,
                    AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
                    AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss,
                    AVG(bars_held) as avg_bars_held
                FROM paper_positions
                WHERE run_id = %s AND status = 'CLOSED';
            """, (run_id,))
            stats = cur.fetchone()
            
            # By setup
            cur.execute("""
                SELECT 
                    setup_id,
                    COUNT(*) as trades,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winners,
                    SUM(realized_pnl) as pnl,
                    AVG(r_multiple) as avg_r
                FROM paper_positions
                WHERE run_id = %s AND status = 'CLOSED'
                GROUP BY setup_id
                ORDER BY pnl DESC;
            """, (run_id,))
            by_setup = cur.fetchall()
            
            # By symbol (top 10 by PnL)
            cur.execute("""
                SELECT 
                    symbol,
                    COUNT(*) as trades,
                    SUM(realized_pnl) as pnl,
                    AVG(r_multiple) as avg_r
                FROM paper_positions
                WHERE run_id = %s AND status = 'CLOSED'
                GROUP BY symbol
                ORDER BY pnl DESC
                LIMIT 10;
            """, (run_id,))
            top_symbols = cur.fetchall()
            
            # Daily P&L
            cur.execute("""
                SELECT 
                    DATE(exit_ts) as date,
                    COUNT(*) as trades,
                    SUM(realized_pnl) as pnl
                FROM paper_positions
                WHERE run_id = %s AND status = 'CLOSED'
                GROUP BY DATE(exit_ts)
                ORDER BY date;
            """, (run_id,))
            daily_pnl = cur.fetchall()
    
    return {
        "run_id": str(run_id),
        "started_at": started_at.isoformat() if started_at else None,
        "overall": {
            "total_trades": stats[0] or 0,
            "winners": stats[1] or 0,
            "losers": stats[2] or 0,
            "win_rate": (stats[1] / stats[0]) if stats[0] else 0,
            "total_pnl": float(stats[3] or 0),
            "avg_r": float(stats[4] or 0),
            "best_r": float(stats[5] or 0),
            "worst_r": float(stats[6] or 0),
            "avg_win": float(stats[7] or 0),
            "avg_loss": float(stats[8] or 0),
            "avg_bars_held": float(stats[9] or 0),
        },
        "by_setup": [
            {
                "setup_id": row[0],
                "trades": row[1],
                "winners": row[2],
                "win_rate": row[2] / row[1] if row[1] else 0,
                "pnl": float(row[3] or 0),
                "avg_r": float(row[4] or 0),
            }
            for row in by_setup
        ],
        "top_symbols": [
            {
                "symbol": row[0],
                "trades": row[1],
                "pnl": float(row[2] or 0),
                "avg_r": float(row[3] or 0),
            }
            for row in top_symbols
        ],
        "daily_pnl": [
            {
                "date": row[0].isoformat() if row[0] else None,
                "trades": row[1],
                "pnl": float(row[2] or 0),
            }
            for row in daily_pnl
        ],
    }


# =============================================================================
# Position Control
# =============================================================================

@router.post("/positions/{position_id}/close")
def close_position(position_id: str):
    """
    Manually close a position at current market price.
    """
    # Get current price from Binance
    import requests
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get position
            cur.execute("""
                SELECT pp.id, pp.run_id, pp.symbol, pp.side,
                       pp.entry_price_with_slippage, pp.position_size,
                       pp.risk_amount, pp.notional_value, pp.fees_paid
                FROM paper_positions pp
                WHERE pp.id = %s AND pp.status = 'OPEN';
            """, (position_id,))
            row = cur.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Open position not found")
            
            pos_id, run_id, symbol, side, entry, size, risk, notional, fees = row
            
            # Get current price
            try:
                resp = requests.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": symbol},
                    timeout=5,
                )
                resp.raise_for_status()
                current_price = float(resp.json()["price"])
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to get price: {e}")
            
            # Apply slippage
            slip = current_price * (5.0 / 10000)
            if side == "long":
                exit_with_slip = current_price - slip
            else:
                exit_with_slip = current_price + slip
            
            # Calculate PnL
            if side == "long":
                pnl = (exit_with_slip - entry) * size
            else:
                pnl = (entry - exit_with_slip) * size
            
            exit_fee = current_price * size * (4.0 / 10000)
            total_fees = fees + exit_fee
            realized_pnl = pnl - total_fees
            r_multiple = pnl / risk if risk > 0 else 0
            
            now = datetime.now(timezone.utc)
            
            # Update position
            cur.execute("""
                UPDATE paper_positions
                SET status = 'CLOSED',
                    current_price = %s,
                    exit_ts = %s,
                    exit_price = %s,
                    exit_price_with_slippage = %s,
                    exit_reason = 'MANUAL',
                    realized_pnl = %s,
                    fees_paid = %s,
                    r_multiple = %s,
                    updated_at = %s
                WHERE id = %s;
            """, (
                current_price, now, current_price, exit_with_slip,
                realized_pnl, total_fees, r_multiple, now, pos_id,
            ))
            
            # Update account
            win = 1 if realized_pnl > 0 else 0
            loss = 1 if realized_pnl <= 0 else 0
            
            cur.execute("""
                UPDATE paper_accounts
                SET current_capital = current_capital + %s,
                    current_exposure = current_exposure - %s,
                    total_trades = total_trades + 1,
                    winning_trades = winning_trades + %s,
                    losing_trades = losing_trades + %s,
                    total_pnl = total_pnl + %s,
                    total_fees = total_fees + %s,
                    updated_at = %s
                WHERE run_id = %s;
            """, (
                realized_pnl, notional, win, loss,
                realized_pnl, total_fees, now, run_id,
            ))
        
        conn.commit()
    
    return {
        "position_id": position_id,
        "symbol": symbol,
        "side": side,
        "exit_price": exit_with_slip,
        "exit_reason": "MANUAL",
        "realized_pnl": realized_pnl,
        "r_multiple": r_multiple,
        "message": "Position closed successfully",
    }
