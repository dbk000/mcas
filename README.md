# MCAS - Market Cognition At Scale

**Version 7.0** - Complete Trading System with Gap Analysis Fixes

## Overview

MCAS is a sophisticated crypto trading analytics system that scans hundreds of markets, generates ranked signals, and executes paper trades with full position management. This version addresses all gaps identified in the scope reconciliation analysis.

## What's New in v7

### Strategy Improvements (S1)
- **Fixed "ripe" condition**: Now properly checks BB width slope is turning up on 2 of last 3 bars (was hardcoded to True)
- **Fixed squeeze reset logic**: Now requires 2 consecutive bars out of squeeze before resetting squeeze_age (was immediate)
- **Proper time stop**: T_dead = 4 hours (4 bars on 1h TF) instead of generic 12 bars

### Strategy Improvements (S2)
- **Added BTC shock filter**: Skip longs if BTC dumping (RSI < 30, vol_z > 2), skip shorts if BTC pumping
- **Added pre-cross t-1 check**: Prevents triggering on exact golden/death cross bar
- **Proper time stop**: T_dead = 24 hours for this slower setup

### Exit Logic Enhancements
- **Fail-Fast Exit**: If price re-enters squeeze box within N bars (S1 only), exit immediately
- **Time Stop (T_dead)**: Exit if position hasn't made 0.5R progress within time limit
- **Trailing Stop**: After TP1 hit, trail from high water mark to lock in gains

### Risk Management
- **Daily Trade Cap**: Maximum 20 trades per day to prevent overtrading
- **Per-Asset Cooldown**: 6-hour cooldown after stop-out (no immediate re-entry)
- **Daily Loss Circuit Breaker**: Configurable max daily loss limit
- **Proper Position Sizing**: Risk-based sizing using account capital and R-value

### Code Quality
- **Centralized Direction enum**: Moved to setup_base.py (was in S1, imported by S2)
- **Complete database schema**: All tables properly defined in mcas_schema.sql
- **Exit parameters in trace_json**: Box levels and time stops stored for position monitoring

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           ARCHITECTURE v7                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│    ┌──────────────────────────────────────────────────────────────┐     │
│    │                      API Layer                                │     │
│    │  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌─────────────┐   │     │
│    │  │  main    │ │  control   │ │   runs   │ │   trading   │   │     │
│    │  │ (health) │ │ (commands) │ │ (queries)│ │ (account)   │   │     │
│    │  └──────────┘ └────────────┘ └──────────┘ └─────────────┘   │     │
│    └───────────────────────────┬──────────────────────────────────┘     │
│                                │                                         │
│          ┌─────────────────────┴─────────────────────┐                  │
│          ▼                                           ▼                  │
│  ┌─────────────────┐                       ┌─────────────────┐         │
│  │    Postgres     │                       │      Redis      │         │
│  │  ┌───────────┐  │                       │  ┌───────────┐  │         │
│  │  │   runs    │  │                       │  │  prices   │  │         │
│  │  │  trades   │  │                       │  │  pub/sub  │  │         │
│  │  │  paper_*  │  │                       │  │  status   │  │         │
│  │  │ run_queue │  │                       │  └───────────┘  │         │
│  │  │   ohlcv   │  │                       └────────┬────────┘         │
│  │  └───────────┘  │                                │                  │
│  └────────┬────────┘                                │                  │
│           │                                         │                  │
│           ▼                                         │                  │
│  ┌─────────────────────────────────────────────────┴────────────────┐  │
│  │                        Worker v7                                  │  │
│  │  ┌────────────────────────────────────────────────────────────┐  │  │
│  │  │   Queue Consumer + Position Monitor + Auto-Trade Bridge    │  │  │
│  │  └────────────────────────────────────────────────────────────┘  │  │
│  │                                                                   │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │  │
│  │  │   Signal    │  │   Paper     │  │     Position Monitor    │  │  │
│  │  │   Engine    │  │  Trading    │  │  (fail-fast, time-stop, │  │  │
│  │  │ (S1, S2)    │  │   Engine    │  │   trailing, TP1/TP2)    │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Gap Analysis Summary

### Addressed in v7

| Gap | Status | Implementation |
|-----|--------|----------------|
| S1 ripe condition simplified | ✅ Fixed | Checks BB width slope on 2 of last 3 bars |
| S1 squeeze reset immediate | ✅ Fixed | Requires 2 consecutive bars out before reset |
| S2 missing BTC filter | ✅ Fixed | BTC shock filter (RSI/volume based) |
| S2 pre-cross timing | ✅ Fixed | t-1 check prevents exact-bar triggers |
| Fail-fast exit missing | ✅ Fixed | Checks if price re-enters box within N bars |
| Time stop not aligned | ✅ Fixed | T_dead = 4h (S1) or 24h (S2) |
| Trailing stop missing | ✅ Fixed | Trail from HWM after TP1 hit |
| Daily trade cap | ✅ Fixed | Max 20 trades/day enforced |
| Per-asset cooldown | ✅ Fixed | 6-hour cooldown after stop-out |
| Direction enum location | ✅ Fixed | Centralized in setup_base.py |
| Schema incomplete | ✅ Fixed | Full schema in mcas_schema.sql |

### Remaining for Future

| Gap | Priority | Notes |
|-----|----------|-------|
| WebSocket data feed | Medium | Currently using REST polling |
| OHLCV persistence | Medium | Cold start reloads from exchange |
| Champion/challenger | Low | Single paper run currently |
| Unit tests | Medium | Structure supports testing |

## Exit Conditions

Positions are checked every second with this priority:

| Priority | Exit Type | Condition |
|----------|-----------|-----------|
| 1 | STOP_LOSS | Price hits stop level |
| 2 | FAIL_FAST | Price back in box within N bars (S1) |
| 3 | TIME_STOP | Held > T_dead bars, hasn't made 0.5R |
| 4 | TRAILING_STOP | After TP1 hit, retraces to entry + 0.5R |
| 5 | TP1 | Price hits take-profit 1 |
| 6 | TP2 | Price hits take-profit 2 |

## Position Entry Checks

Before opening a position, the system checks:

1. **One-per-asset**: No existing OPEN/PENDING position for symbol
2. **Cooldown**: No recent stop-out on this asset (6h cooldown)
3. **Daily cap**: Haven't exceeded 20 trades today
4. **Daily loss**: Haven't hit daily loss limit
5. **Max positions**: Haven't reached concurrent position limit
6. **Valid R-value**: Signal has positive risk/reward

## Quick Start

### 1. Deploy
```bash
# Initialize database
docker-compose up -d postgres redis
psql -U trading -d trading -f docs/db/schema/mcas_schema.sql

# Start services
docker-compose up -d
```

### 2. Start Paper Trading
```bash
curl -X POST http://localhost/v1/control/runs/paper/start \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "initial_capital": 10000,
    "auto_trade": true,
    "max_positions": 3,
    "risk_per_trade_pct": 1.0
  }'
```

### 3. Monitor
```bash
# Account status
curl http://localhost/v1/trading/account -H "X-API-Key: YOUR_KEY"

# Open positions
curl http://localhost/v1/trading/positions -H "X-API-Key: YOUR_KEY"

# Performance
curl http://localhost/v1/trading/performance -H "X-API-Key: YOUR_KEY"
```

## File Structure

```
trading-backend/
├── api/
│   ├── app/
│   │   ├── main.py          # FastAPI app + health
│   │   ├── runs_api.py      # Run query endpoints
│   │   ├── control_api.py   # Control plane endpoints
│   │   └── trading_api.py   # Trading data endpoints
│   ├── requirements.txt
│   └── Dockerfile
├── worker/
│   ├── app/
│   │   ├── worker.py        # Main loop + position monitoring
│   │   ├── signal_engine.py # Feature Factory + Signal ranking
│   │   ├── setup_base.py    # Shared types (Direction, ExitType)
│   │   ├── setups/
│   │   │   ├── s1_bb_squeeze.py    # S1 v2.1 (fixed ripe/reset)
│   │   │   └── s2_golden_cross.py  # S2 v1.4 (BTC filter, t-1)
│   │   ├── price_feed.py    # Real-time price + position monitor
│   │   ├── paper_trading.py # Paper trading engine
│   │   ├── run_executor.py  # SCAN/BACKTEST/PAPER execution
│   │   ├── indicators.py    # Indicator computation
│   │   ├── execution.py     # Slippage/fee models
│   │   └── data_layer.py    # OHLCV persistence
│   ├── requirements.txt
│   └── Dockerfile
├── docs/
│   └── db/
│       └── schema/
│           └── mcas_schema.sql  # Complete database schema
├── caddy/
│   └── Caddyfile
├── docker-compose.yml
└── .env.example
```

## API Endpoints

### Control API (`/v1/control/...`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/control/runs/scan` | POST | Queue a SCAN run |
| `/v1/control/runs/backtest` | POST | Queue a BACKTEST run |
| `/v1/control/runs/paper/start` | POST | Start PAPER trading |
| `/v1/control/runs/paper/stop` | POST | Stop PAPER trading |

### Trading API (`/v1/trading/...`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/trading/account` | GET | Account balance and stats |
| `/v1/trading/positions` | GET | List positions (open/closed) |
| `/v1/trading/positions/{id}` | GET | Position details with trace |
| `/v1/trading/positions/{id}/close` | POST | Manually close position |
| `/v1/trading/trades` | GET | Trade history |
| `/v1/trading/performance` | GET | Performance analytics |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | required | Postgres connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `API_KEY` | required | API authentication |
| `SCAN_INTERVAL_SECONDS` | `300` | Periodic scan interval |
| `ENABLE_PERIODIC_SCANS` | `true` | Enable/disable auto-scans |
| `ENABLE_POSITION_MONITORING` | `true` | Enable position monitoring |
| `SYMBOL_COUNT` | `200` | Number of symbols |
| `SETUP_IDS` | `S1,S2` | Active setups |

### Execution Config Defaults

| Parameter | Value | Description |
|-----------|-------|-------------|
| Entry slippage | 5 bps | 0.05% adverse |
| Exit slippage | 5 bps | 0.05% adverse |
| Maker fee | 2 bps | 0.02% |
| Taker fee | 4 bps | 0.04% |
| Max positions | 3 | Concurrent positions |
| Risk per trade | 1% | Of account capital |
| Daily loss limit | 3% | Circuit breaker |

---

**"Go nuts architecturally. Stay boring computationally. That's the winning combo."**
