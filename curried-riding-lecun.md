# Plan: Live Pipeline Feed (Revised after Data Audit)

## Context

Replace the static Glass Box Replay on the landing page with a live auto-scrolling feed of real pipeline events. The user wants the full-superset endpoint first, then will descope fields after reviewing.

## Data Audit Results

- **Production DB**: 76 S9 opportunities have real sealed `trace_json`. All other strategies (D1, U1, EQFP, S10) have the sealing code but aren't persisting traces — the wiring exists but those evaluators aren't passing `trace_json` to `_persist_signal_to_pg()` on the production signal_worker image.
- **Dev DB**: 0 traces (stale data from Feb, different signal_worker build).
- **S9 trace shape** (real data): `v, strategy, symbol, timeframe, eval_ts, gates[], score{}, state{}, promotion{sealed_at}, trace_hash, config_hash, candle_window[], microstructure{}, config_snapshot{}, indicator_params{}, indicator_snapshot{}, indicator_params_hash, cross_sectional_context{}`
- **payload_json** (all 3,579 opps): rich per-strategy data — D1 has score_components, why_items, entry/stop/tp prices; EQFP has predicted_return, top_features, quality factors
- **simulation_results.trace_json**: exit outcomes with indicator values, exit_reason, bars_held
- **Redis**: effectively empty (1 stale key)
- **meta.version_event**: 14 pipeline introspection events (research findings, bug fixes)

## Phase 0: Wire TraceContext for ALL strategies (backend)

The sealing code exists in `signal_worker.py` for S10 (line 628), D1 (line 857), U1 (line 957), S9 (line 1550). S9 works in production. The others need verification that:
1. `trace_json=trace_dict` is passed to `_persist_signal_to_pg()`
2. The production signal_worker image includes this code

**Files:**
- `Backend/governance/signal_worker.py` — verify all 4 evaluators pass trace_json
- Rebuild + deploy signal_worker containers on production

**Verification:** After deploy, wait for new D1/U1/S10 signals to fire, then:
```sql
SELECT setup_id, count(trace_json) FROM opportunities
WHERE created_ts > NOW() - INTERVAL '1 hour' AND trace_json IS NOT NULL
GROUP BY setup_id;
```

## Phase 1: Full-Superset Public Feed Endpoint (backend)

### Create `Backend/api/app/routers/public_feed.py`

**Endpoint:** `GET /v1/public/pipeline-feed`
- No auth (mounted without `Depends(require_api_key)`)
- All operational params governed via `get_config()`:
  - `PUBLIC_FEED_CACHE_TTL_S` (env var, default 10)
  - `PUBLIC_FEED_MAX_EVENTS` (env var, default 50)
- In-memory cache with governed TTL

**Query:** Recent opportunities with trace_json, joined to simulation_results:
```sql
SELECT o.id, o.setup_id, o.symbol, o.timeframe, o.score, o.status,
       o.trace_json, o.payload_json, o.created_ts, o.updated_ts,
       sr.trace_json as sim_trace
FROM opportunities o
LEFT JOIN LATERAL (
    SELECT trace_json FROM simulation_results
    WHERE opportunity_id = o.id ORDER BY created_at DESC LIMIT 1
) sr ON TRUE
WHERE o.trace_json IS NOT NULL
  AND o.created_ts > NOW() - INTERVAL '24 hours'
ORDER BY o.created_ts DESC
LIMIT {max_events from config}
```

**Response — full superset, grouped:**
```json
{
  "data": {
    "events": [{
      "opportunity": {
        "id": 759187, "setup_id": "S9", "symbol": "OGNUSDT",
        "timeframe": "1h", "score": 0.54, "status": "active",
        "created_ts": "...", "updated_ts": "..."
      },
      "pipeline": {
        "v": "1.0", "strategy": "S9", "eval_ts": 1773619502.7,
        "gates": [...], "score": {...}, "state": {...},
        "promotion": { "sealed_at": ..., "setup_version": "S9_v1.0" },
        "trace_hash": "a78e3f...",
        "candle_window": [...], "indicator_snapshot": {...},
        "indicator_params": {...}, "config_snapshot": {...},
        "cross_sectional_context": {...}, "microstructure": {...}
      },
      "simulation": {
        "conditions_met": {...}, "indicator_values": {...},
        "exit_reason": "TP1", "exit_conditions": {...}
      }
    }],
    "count": 50,
    "has_live_data": true
  },
  "meta": { "timestamp": "...", "version": "v1", "compute_ms": 12.3 }
}
```

User will descope fields from this superset after reviewing.

### Add governance params

**Backend:** Add to `get_config()` / env vars:
- `PUBLIC_FEED_CACHE_TTL_S=10`
- `PUBLIC_FEED_MAX_EVENTS=50`

**Frontend bootstrap:** Add to `session.py` `_build_bootstrap_payload` → `frontend_config.polling`:
- `public_feed_ms: 12_000`

**Frontend governed.js DEFAULTS:** Add `public_feed_ms: 12_000` to `polling` block.

### Mount in `Backend/api/app/main.py` (~line 1509)

```python
from api.app.routers.public_feed import router as public_feed_router
app.include_router(public_feed_router, prefix="/v1")  # no auth
```

## Phase 1 (frontend): Live Feed Component

### Create `Frontend/src/hooks/useLiveFeed.js` (~50 lines)

- Native `fetch` (correct — no ApiContext needed for public endpoint)
- Poll interval from `getPollingInterval('public_feed_ms')`
- Timeout/retry from `getRequestConfig()` (unauthenticated ≠ ungoverned)
- `document.visibilitychange` — pause when tab hidden
- `VITE_PUBLIC_API_BASE` env var for production URL
- Returns `{ events, error, isLive }`

### Modify `Frontend/src/pages/landing-layouts/shared.jsx` (~80 lines added)

- Rename `GlassBoxReplay` → `StaticGlassBoxReplay` (private)
- New `GlassBoxReplay` (same export): tries live feed, falls back to static
- `LivePipelineFeed`: fixed-height card, pulsing LIVE/DELAYED badge
- Feed health badge: uses `TF_SECONDS * STALENESS_MULTIPLIER` (from governed.js `getStalenessThresholdS`) to determine LIVE vs DELAYED
- `FeedEvent`: compact row — time, phase, asset_class badge ("crypto"/"equities"), VERIFIED badge
- CSS `feed-enter` animation for new events
- No symbols, no directions in the default UI render (scrubbing at display layer)

## Phase 1.5: Proof-Pack Curation Script

Create `Backend/scripts/curate_proof_pack.py` that:
1. Queries real production data (strategy count, aggregate PF, avg daily PF)
2. Generates `Frontend/public/curation/proof-pack.v1.json` with real numbers
3. Replaces the placeholder "12 strategies", "1.2 to 2.4 PF range" with actuals

This is a launch blocker — wrong numbers on the landing page are a liability.

## Implementation Order

1. **Phase 0**: Verify trace_json wiring for all strategies → rebuild signal_workers
2. **Phase 1 backend**: public_feed.py + governance params + main.py mount → rebuild API
3. **Phase 1 frontend**: useLiveFeed.js + shared.jsx refactor → build + deploy
4. **Phase 1.5**: Curation script → regenerate proof-pack → deploy
5. **User review**: descope fields from the superset based on what looks right

## Critical Files

| File | Action |
|---|---|
| `Backend/governance/signal_worker.py` | Verify trace_json wiring for D1/U1/S10/EQFP |
| `Backend/api/app/routers/public_feed.py` | NEW — full superset public endpoint |
| `Backend/api/app/main.py:~1509` | Mount public feed router (no auth) |
| `Backend/api/app/routers/session.py` | Add `public_feed_ms` to bootstrap polling |
| `Frontend/src/config/governed.js` | Add `public_feed_ms` default |
| `Frontend/src/hooks/useLiveFeed.js` | NEW — polling hook with governed intervals |
| `Frontend/src/pages/landing-layouts/shared.jsx` | Refactor GlassBoxReplay + LivePipelineFeed |
| `Backend/scripts/curate_proof_pack.py` | NEW — proof-pack curation from real data |

## Verification

```bash
# Phase 0: traces being written
docker exec backend-postgres-1 psql -U trading -d trading -c "
SELECT setup_id, count(trace_json) as traces
FROM opportunities
WHERE created_ts > NOW() - INTERVAL '1 hour' AND trace_json IS NOT NULL
GROUP BY setup_id"

# Phase 1 backend: endpoint works without auth
curl -s http://localhost:8002/v1/public/pipeline-feed | python3 -m json.tool | head -40

# Phase 1 frontend: build passes
cd Frontend && npm run build

# Phase 1.5: proof-pack has real numbers
python3 -c "import json; d=json.load(open('Frontend/public/curation/proof-pack.v1.json')); print(d.get('source_status'))"
# Should print 'live' not 'placeholder'

# Full flow: mcasui.com shows live feed or graceful fallback
```
