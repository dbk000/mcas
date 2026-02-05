# MCAS Backend — Implementation Status

## Last Updated: 2026-02-03

## Current State: FULLY OPERATIONAL

All services running via Docker Compose. Redis live cache retrofit complete.

### Running Services (7)
| Service | Status | Notes |
|---------|--------|-------|
| api | ✅ Running | Port 8002→8000, `/v1/opportunities` reads Redis |
| worker | ✅ Running | Periodic scans every 5min |
| ingestor | ✅ Running | WebSocket connected, 193/200 symbols |
| indicator_worker | ✅ Running | Stream consumer, reconciles every 60s |
| signal_worker | ✅ Running | Evaluates 192 symbol/tf pairs |
| postgres | ✅ Running | Schema applied manually (see below) |
| redis | ✅ Running | 600 candles/symbol, indicators cached |

### Verified Working
- `docker compose exec redis redis-cli LLEN candle:BTCUSDT:1m` → 600
- `curl http://localhost:8002/health` → `{"status":"healthy"}`
- `curl -H "X-API-Key: $KEY" http://localhost:8002/v1/opportunities` → `"source": "redis_live"`

### Schema Notes
The `mcas_schema.sql` file is OUT OF SYNC with the code. During initial setup, these manual fixes were required:

```sql
-- runs table additions
ALTER TABLE runs ADD COLUMN notes text;
ALTER TABLE runs ADD COLUMN engine_commit text;
ALTER TABLE runs ADD COLUMN parent_run_id uuid REFERENCES runs(id);
ALTER TABLE runs ADD COLUMN finished_at timestamptz;
ALTER TABLE runs ADD COLUMN error_text text;
CREATE UNIQUE INDEX idx_runs_fingerprint_type ON runs(fingerprint, run_type);

-- run_events: code expects different columns than schema
DROP TABLE run_events CASCADE;
CREATE TABLE run_events (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts timestamptz NOT NULL DEFAULT now(),
    event_type text NOT NULL,
    message text NOT NULL DEFAULT '',
    payload_json text
);

-- run_trades: code uses 'asset' not 'symbol', different column names
DROP TABLE run_trades CASCADE;
CREATE TABLE run_trades (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    asset text NOT NULL,
    timeframe text NOT NULL,
    side text NOT NULL,
    setup_id text NOT NULL,
    signal_bar_ts timestamptz,
    since_ts timestamptz,
    action_by_ts timestamptz,
    entry_ts timestamptz,
    entry_price float,
    entry_bar_index int,
    stop_price float,
    tp1_price float,
    tp2_price float,
    r_value float,
    setup_score float,
    exit_ts timestamptz,
    exit_price float,
    exit_reason text,
    r_multiple float,
    pnl_gross float,
    fees float,
    slippage float,
    pnl_net float,
    trace_json text,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

### Environment
- `.env` created from `.env.example`
- `API_KEY=0cf312bc5db6c7c6f265629e24069b4d735933acbd62514d814538b580b49c2d`
- API exposed on port 8002 (8000/8001 occupied on host)

### UI Deployment (Pending)
- Caddyfile updated for `api.mcasui.com` with CORS for `https://mcasui.com`
- Frontend to be deployed to Cloudflare Pages
- DNS: need A record `api.mcasui.com` → VPS IP

### Key Files Modified This Session
- `worker/app/signal_worker.py` — added `get_candles_as_ohlcv` import, populate snapshot.ohlcv
- `api/app/main.py` — `_read_opportunities_from_redis` returns `None` vs `[]` to prevent incorrect Postgres fallback
- `docker-compose.yml` — API port 8002, Caddy ports 80/443
- `caddy/Caddyfile` — HTTPS config for api.mcasui.com with CORS
