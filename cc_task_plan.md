# CC Task Plan — D1 Cold-Start, S9 Regime Gate, Alert Watchdog
_2026-03-18_

## 1. D1 Cold-Start Bootstrap

### Problem
`_d1_prev_states` is an in-memory dict. Container restart = cold start. D1 needs 48h
of 1h bars to reach DORMANT_CONFIRMED. The data already exists in Postgres.

### Design

At signal_worker startup, after bootstrap_genesis() and strategy registration, before
the main eval loop:

1. Read `D1_DORMANCY_LOOKBACK` from D1TypedConfig (= 48 bars)
2. Compute replay window: `int(DORMANCY_LOOKBACK * 1.5)` = 72 bars (documented derivation)
3. Get monitored universe from Redis
4. For each symbol in universe (filtered by `is_d1_excluded()`):
   a. Load `200 + replay_window` 1h bars from Postgres ohlcv table (200 = indicator warmup)
   b. Skip symbols with < replay_window bars (don't synthesise)
   c. Create FeatureFactory, compute indicator snapshots for the last `replay_window` bars
   d. Feed each snapshot through `d1_evaluator.compute_state()` chronologically (oldest first)
   e. Store final state in `_d1_prev_states[f"{symbol}:1h"]`
5. Log: `"D1 state bootstrapped: {n} symbols, oldest state from {ts}, newest from {ts}"`

### Governance Requirements
- Replay window derived from `get_config().D1.DORMANCY_LOOKBACK` (= 48), NOT hardcoded 72
- OHLCV from Postgres (governed persistence layer), NOT Redis
- FeatureFactory for indicator computation (same code path as live)
- `is_d1_excluded()` applied (same filter as live eval)
- Add `D1_BOOTSTRAP_WARMUP_MULTIPLIER: float = 1.5` to D1TypedConfig (governs the 1.5× buffer)

### File Changes
- `governance/signal_worker.py` — new method `_bootstrap_d1_states()`, called in `__init__`
  or `run()` before main loop
- `governance/config_registry.py` — add `BOOTSTRAP_WARMUP_MULTIPLIER: float = 1.5` to D1TypedConfig

### Verification
After restart, within 1 eval cycle:
```bash
docker compose logs --tail=5 signal_worker_0 | grep "D1 state bootstrapped"
# Should show: "D1 state bootstrapped: ~350 symbols, oldest from ..., newest from ..."
docker compose logs --tail=20 signal_worker_0 | grep "d1_signal\|D1.*phase"
# D1 should show non-SCANNING phases for qualifying symbols
```

### Performance
- 397 symbols × 272 bars × FeatureFactory.compute() — ~30-60s at boot
- Acceptable one-time cost. Main loop blocked during bootstrap (by design — no
  partial state allowed).
- Parallelise with ThreadPoolExecutor if > 120s (but likely unnecessary for 397 symbols)

---

## 2. S9 Regime Gate (Fear & Greed)

### Problem
S9 7d PF = 0.571 across 37 signals. Breadth gate is validated — don't touch it.
S9 fires during brief bear-market breadth spikes without regime awareness.

### Existing Infrastructure
- `fetch_fear_greed_index()` in `governance/d2_paper_trader.py` line 257
- Calls `https://api.alternative.me/fng/?limit=1`
- Returns `{"value": int 0-100, "classification": str}`
- Cached for 24h
- Currently "log_only" mode in D2 (stored in metadata, not enforced)

### Design

Extract `fetch_fear_greed_index()` from d2_paper_trader.py into a shared utility
(governance layer, since it's used by multiple workers). Wire into S9 evaluation.

**Gate logic:** S9 fires shorts — mean reversion works best in fearful markets.
- FnG ≤ `S9_FNG_MAX_VALUE` (default 50): signals ALLOWED
- FnG > `S9_FNG_MAX_VALUE`: signals SUPPRESSED
- FnG unavailable (API down): signals SUPPRESSED (fail-closed). S9 PF 0.571 proved
  firing without regime awareness repeats the exact failure. A missed signal is a zero.
  A bad signal is a loss. Log warning, increment `s9_fng_unavailable` counter.

**Implementation:**
1. Move `fetch_fear_greed_index()` to `governance/regime.py` (new file, shared utility)
2. In signal_worker `_evaluate_s9_cross_sectional()`, before emitting signals:
   - Fetch FnG value (cached, no API call per eval)
   - If `fng_value > S9_FNG_MAX_VALUE` and gate enabled: skip, log, increment diagnostic
3. Add to diagnostics: `s9_fng_gated: int` counter
4. Record FnG value in S9 trace_json for Glass Box auditability

### Governance Requirements
- Add to WorkerTypedConfig (or S9TypedConfig if one exists):
  - `S9_FNG_GATE_ENABLED: bool = True`
  - `S9_FNG_MAX_VALUE: int = 50` (Fear/Extreme Fear only)
- FnG threshold in TypedConfigRegistry, NOT hardcoded in signal_worker
- Process crashes if config missing (frozen registry pattern)
- FnG value recorded in trace_json for every S9 eval (Glass Box compliance)

### File Changes
- `governance/regime.py` — new file, `fetch_fear_greed_index()` extracted from d2_paper_trader
- `governance/d2_paper_trader.py` — import from regime.py instead of local function
- `governance/signal_worker.py` — add FnG check in `_evaluate_s9_cross_sectional()`
- `governance/config_registry.py` — add S9 FnG config fields to WorkerTypedConfig

### Validation
Backtest the gate against the 37 signals from March 14-17:
```sql
-- Get FnG values for each signal date
-- Then count how many would have been filtered at threshold 50
```
Also fetch historical FnG from API: `https://api.alternative.me/fng/?limit=7`
Cross-reference with the 37 signal dates. Report:
- N signals that would have passed the FnG ≤ 50 gate
- N signals that would have been filtered
- PF of the surviving signals vs all 37

---

## 3. Alert Watchdog

### Problem
3-day silent blackout (March 11-13) and 36-hour S9 NameError both went undetected.
Solo operator running a marathon on Sunday cannot monitor manually.

### Design

`Backend/scripts/alert_watchdog.py` — governed script.

```python
#!/usr/bin/env python3
"""Governed alert watchdog. Reads thresholds from TypedConfigRegistry."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from governance.bootstrap import bootstrap_genesis, get_config
bootstrap_genesis()

# Read ALL thresholds from frozen registry — crash if missing
config = get_config().Alert
STALE_HOURS = config.SIGNAL_STALE_HOURS
RESTART_THRESHOLD = config.CONTAINER_RESTART_THRESHOLD
WEBHOOK_URL = config.WEBHOOK_URL
if not WEBHOOK_URL:
    print("FATAL: ALERT_WEBHOOK_URL is empty"); os._exit(78)
```

**Checks:**
1. Signal staleness: `SELECT MAX(created_ts) FROM opportunities WHERE batch_id IS NULL`
   - If age > STALE_HOURS: ALERT
2. Signal worker errors: check Postgres for recent error-level log entries or
   query signal_worker diagnostics from Redis. Lightweight, no Docker socket needed.
   - Container restart monitoring deferred post-Rome (signal staleness catches
     the same failure: crashed container = no signals = stale alert triggers)

**Alert payload:**
```json
{
  "content": "🚨 MCAS ALERT\n• Signals stale: 14.2h (threshold: 6h)\n• Last signal: 2026-03-17 00:05 UTC\n• Container restarts: social_collector=47\n• Signal worker errors: 3"
}
```

**Webhook:** POST to Discord/Telegram webhook URL from config.

### Governance Requirements
- New `AlertTypedConfig` dataclass in config_registry.py:
  ```python
  @dataclass
  class AlertTypedConfig:
      SIGNAL_STALE_HOURS: int = 6
      CONTAINER_RESTART_THRESHOLD: int = 3
      WEBHOOK_URL: str = ""
  ```
- Registered in bootstrap.py `_populate_config()`
- Read from env: `ALERT_SIGNAL_STALE_HOURS`, `ALERT_CONTAINER_RESTART_THRESHOLD`, `ALERT_WEBHOOK_URL`
- Process crashes if WEBHOOK_URL is empty (non-negotiable for a monitoring script)
- Script itself calls bootstrap_genesis() — full governance compliance

### File Changes
- `scripts/alert_watchdog.py` — new file
- `governance/config_registry.py` — add AlertTypedConfig
- `governance/bootstrap.py` — register AlertTypedConfig in `_populate_config()`

### Cron Setup
```bash
# Add to crontab (runs every 6 hours)
0 */6 * * * cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/alert_watchdog.py >> /var/log/mcas_watchdog.log 2>&1
```

### Verification
```bash
# Test run (should send an alert since signals are currently stale)
cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/alert_watchdog.py
# Check webhook destination received the message
```

---

## Execution Order

All three items are independent — can execute in parallel, deploy sequentially.

```
Track 1: D1 Cold-Start Bootstrap
  1a. Add BOOTSTRAP_WARMUP_MULTIPLIER to D1TypedConfig
  1b. Write _bootstrap_d1_states() in signal_worker.py
  1c. Wire into startup sequence (after strategy registration, before main loop)

Track 2: S9 Regime Gate
  2a. Add S9_FNG_GATE_ENABLED + S9_FNG_MAX_VALUE to WorkerTypedConfig
  2b. Extract fetch_fear_greed_index() to governance/regime.py
  2c. Wire into _evaluate_s9_cross_sectional()
  2d. Validate against 37 historical signals

Track 3: Alert Watchdog
  3a. Create AlertTypedConfig in config_registry.py
  3b. Register in bootstrap.py
  3c. Write scripts/alert_watchdog.py
  3d. Test run (expect alert — signals currently stale)
```

### Deploy Sequence (sequential, independently verified)

```
Step 1: Commit + push all changes
Step 2: git pull on /srv/mcas
Step 3: Build affected images

Step 4: Restart signal_worker_0/1 (D1 bootstrap + S9 FnG gate)
  Verify: "D1 state bootstrapped: X symbols"
  Verify: No errors in logs
  Verify: FnG gate diagnostic counter appears

Step 5: Test alert_watchdog.py
  Run: docker compose run --rm -T worker python3 /scripts/alert_watchdog.py
  Verify: webhook receives alert (signals are currently stale)

Step 6: Install cron
  Verify: crontab -l shows the entry
```

---

## Governance Checklist

- [ ] `D1_BOOTSTRAP_WARMUP_MULTIPLIER` in D1TypedConfig (frozen at bootstrap)
- [ ] Replay window derived from `get_config().D1.DORMANCY_LOOKBACK * MULTIPLIER`
- [ ] `S9_FNG_GATE_ENABLED` and `S9_FNG_MAX_VALUE` in WorkerTypedConfig
- [ ] FnG threshold read from frozen registry, not hardcoded
- [ ] FnG value recorded in S9 trace_json (Glass Box)
- [ ] `AlertTypedConfig` registered in bootstrap with SIGNAL_STALE_HOURS,
  CONTAINER_RESTART_THRESHOLD, WEBHOOK_URL
- [ ] Alert script calls bootstrap_genesis() — full governance compliance
- [ ] Alert script crashes if WEBHOOK_URL empty (process death on missing dep)
- [ ] No hardcoded values outside registries
- [ ] No bare os.getenv() outside bootstrap.py
