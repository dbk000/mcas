# Champion/Challenger Framework - Implementation Plan (v8)

**Status**: Pending user approval
**Last Updated**: 2026-02-03

---

## 1) Overview

The Champion/Challenger (C/C) framework enables continuous, guardrailed improvement of MCAS trading strategies through:

- **Parallel evaluation**: Run Baseline + Champion + Challenger simultaneously (3-way comparison)
- **Promotion gates**: Strict rules ensuring only genuine improvements are promoted
- **Complexity control**: One-module-different rule, weekly change cadence enforced
- **Losers-first learning**: Postmortem emphasis on failures to guide next iteration

**Canon intent**: Walk-forward optimization in live time, with guardrails.

---

## 2) Key Constraints (from Scope Doc)

### 2.1 Non-Negotiables

1. **One-module-different rule**: Challenger differs from Champion by exactly one module (or parameter bundle)
2. **Promotion gate (all must pass)**:
   - Expectancy improves
   - Drawdown does not worsen beyond tolerance
   - Improvement generalizes across multiple assets
3. **Human-in-loop**: MVP remains manual execution; C/C applies to signal generation
4. **Complexity control**: One module change per week max; keep baseline running forever
5. **Baseline always running**: Dead-simple baseline evaluates in parallel forever (canon-critical)

**"Baseline runs forever" interpretation (v6: canon-compliant)**:

**MVP implements Option B**: Baseline is ALWAYS evaluated alongside normal evaluation, even when no CC experiment run is active. This achieves unarguable gospel-compliance with the canon's "dead-simple baseline running forever" requirement.

Implementation:
- When CC run is active: 3-way comparison (baseline + champion + challenger)
- When NO CC run is active: Baseline still evaluates in shadow mode alongside the current champion
- Baseline metrics accumulate continuously, providing persistent reference data

This avoids the ambiguity of "baseline only during CC runs" and ensures baseline data is always fresh for comparison when a new experiment starts.

### 2.2 MVP Scope

**In scope**:
- Versioned champion/challenger/baseline variants
- One-module-different enforcement with weekly cadence check
- Parallel evaluation in SHADOW mode (minimum) - 3-way: baseline + champ + chall
- Metrics aggregation + per-asset breakdown
- Promotion gate logic
- Failure bucketizer + "next tweak" recommendation
- Shadow trade simulation using existing execution models

**Out of scope**:
- Automated hyperopt/parameter search
- Multi-armed bandit allocation
- Regime-conditioned champion selection
- Automated execution

---

## 3) Architecture

### 3.1 Core Concepts

```
┌─────────────────────────────────────────────────────────────┐
│                    VARIANT REGISTRY                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐            │
│  │ S1@1.0.0   │  │ S1@2.0.3   │  │ S1@2.0.4   │            │
│  │ BASELINE   │  │ CHAMPION   │  │ CHALLENGER │            │
│  └────────────┘  └────────────┘  └────────────┘            │
│        │               │               │                     │
│        └───────────────┼───────────────┘                    │
│                        │ diff: EXIT module                   │
└────────────────────────┼────────────────────────────────────┘
                         │
┌────────────────────────┼────────────────────────────────────┐
│           EXPERIMENT RUN (3-way parallel)                    │
│  ┌─────────────────────────────────────────────────┐        │
│  │  Same market stream → All 3 variants eval       │        │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐      │        │
│  │  │ Baseline │  │ Champion │  │Challenger│       │        │
│  │  │ outcome  │  │ outcome  │  │ outcome  │       │        │
│  │  └──────────┘  └──────────┘  └──────────┘      │        │
│  └─────────────────────────────────────────────────┘        │
│                         │                                    │
│  ┌─────────────────────────────────────────────────┐        │
│  │  SHADOW TRADE SIMULATOR (reuses ExecutionSpec)  │        │
│  │  - Same slippage/fee model as paper trading     │        │
│  │  - Same TimeStop/exit logic                     │        │
│  │  - Bar-by-bar outcome simulation                │        │
│  └─────────────────────────────────────────────────┘        │
│                         │                                    │
│  ┌─────────────────────────────────────────────────┐        │
│  │  METRICS AGGREGATION                            │        │
│  │  - expectancy, drawdown, win_rate per variant   │        │
│  │  - per_asset breakdown                          │        │
│  └─────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              PROMOTION ENGINE                                │
│  Gate 1: expectancy_challenger > expectancy_champion         │
│  Gate 2: drawdown_challenger <= drawdown_champion + tol      │
│  Gate 3: improvement on ≥ X assets with ≥ N trades each      │
│          (fails explicitly if insufficient sample size)      │
│                                                              │
│  ALL PASS → Promote Challenger to Champion                   │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Artifact Types

Three "versionable candidate" types:

| Type | Example | Description |
|------|---------|-------------|
| **Setup Variant** | S1@2.0.3 vs S1@2.0.4 | Full setup version |
| **Module Variant** | exit.trailing.v1 vs exit.trailing.v2 | Sub-component swap |
| **Parameter Bundle** | {ADX_min: 18} vs {ADX_min: 22} | Param change treated as one module |
| **Score Component** | {W_SEVERITY: 0.3} vs {W_SEVERITY: 0.4} | Scoring weight change |

### 3.3 Module Kinds (Canonical Rotation Order)

Per canon, when choosing what to tweak next, follow this priority:

| Priority | module_kind | Description |
|----------|-------------|-------------|
| 1 | FILTER | Trade eligibility / liquidity filters |
| 2 | REGIME | Regime tagging |
| 3 | ENTRY | Entry triggers |
| 4 | STOP | Stops / invalidation |
| 5 | EXIT | Take-profit / trailing |
| 6 | SIZING | Position sizing |
| 7 | SCORE | Weights (last) |

**Diff-key semantics**:
- `module_kind` (enum): FILTER, REGIME, ENTRY, STOP, EXIT, SIZING, SCORE
- `diff_key` (string): Descriptive pointer like "EXIT.trailing" or "ENTRY.trigger"
- Actual swap: At `module_kind=EXIT`, swap `module_id=exit.trailing.v1` → `exit.trailing.v2`

---

## 4) Data Model

### 4.1 New Tables (Postgres)

**⚠️ CRITICAL: MIGRATION-ONLY SCHEMA ⚠️**

The SQL below is the **migration file content** to be placed in:
```
Backend/docs/db/migrations/YYYYMMDD_HHMMSS_add_cc_tables.sql
```

**DO NOT:**
- Execute this SQL from application code (API routes, worker init, etc.)
- Copy these statements into Python files
- Run at worker startup or request time

**DO:**
- Place in a migration file
- Run via migration runner at deploy time (before app starts)
- Use Alembic, Flyway, or the project's existing migration pattern

**Why this matters:**
- Multiple workers executing `CREATE TABLE` simultaneously = race conditions
- DDL acquires table locks = blocks all queries during execution
- Ad-hoc execution = schema drift between environments
- No migration tracking = no rollback path, no audit trail

**v8 Implementation requirement:**
```python
# IN worker/app/main.py or api/app/main.py:
# ❌ WRONG - DO NOT DO THIS:
# db.execute(open("schema.sql").read())

# ✅ CORRECT - Schema applied externally before app starts:
# $ alembic upgrade head  # Run in deploy script, not app code
```

---

**Migration file: `add_cc_tables.sql`**

```sql
-- =============================================================================
-- MIGRATION: add_cc_tables
-- =============================================================================
-- File: Backend/docs/db/migrations/YYYYMMDD_HHMMSS_add_cc_tables.sql
--
-- DO NOT EXECUTE FROM APPLICATION CODE.
-- Apply via: psql -f <this_file> OR alembic upgrade head
-- =============================================================================

-- PREREQUISITE: Ensure pgcrypto extension is available
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- =============================================================================
-- CHAMPION/CHALLENGER: VARIANT REGISTRY
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_variants (
    variant_id TEXT PRIMARY KEY,              -- e.g., "S1@2.0.3"
    setup_id TEXT NOT NULL,                   -- e.g., "S1", "S2", "U1"
    status TEXT NOT NULL DEFAULT 'CHALLENGER'
        CHECK (status IN ('CHAMPION', 'CHALLENGER', 'BASELINE', 'RETIRED')),

    -- Lineage
    parent_variant_id TEXT REFERENCES cc_variants(variant_id),

    -- Diff specification (what changed from parent)
    -- module_kind: which slot changed (FILTER/REGIME/ENTRY/STOP/EXIT/SIZING/SCORE)
    -- diff_key: descriptive pointer (e.g., "EXIT.trailing", "ENTRY.trigger")
    diff_type TEXT CHECK (diff_type IN ('MODULE', 'PARAM_BUNDLE', 'SCORE_COMPONENT')),
    diff_module_kind TEXT CHECK (diff_module_kind IN ('FILTER', 'REGIME', 'ENTRY', 'STOP', 'EXIT', 'SIZING', 'SCORE')),
    diff_key TEXT,                            -- Descriptive: "EXIT.trailing"
    diff_description TEXT,                    -- Human-readable

    -- Version info
    version_major INT NOT NULL DEFAULT 1,
    version_minor INT NOT NULL DEFAULT 0,
    version_patch INT NOT NULL DEFAULT 0,

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT DEFAULT 'system',
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_cc_variants_setup_status ON cc_variants(setup_id, status);
CREATE INDEX IF NOT EXISTS idx_cc_variants_parent ON cc_variants(parent_variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: MODULE DEFINITIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_modules (
    module_id TEXT PRIMARY KEY,               -- e.g., "exit.trailing.v1"
    module_kind TEXT NOT NULL
        CHECK (module_kind IN ('FILTER', 'REGIME', 'ENTRY', 'STOP', 'EXIT', 'SIZING', 'SCORE')),
    version TEXT NOT NULL,

    -- Implementation
    implementation_ref TEXT NOT NULL,         -- Python import path
    config_schema JSONB,                      -- JSON schema for params

    -- Metadata
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- CHAMPION/CHALLENGER: VARIANT MODULE MAP
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_variant_modules (
    id SERIAL PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id) ON DELETE CASCADE,
    module_kind TEXT NOT NULL
        CHECK (module_kind IN ('FILTER', 'REGIME', 'ENTRY', 'STOP', 'EXIT', 'SIZING', 'SCORE')),
    module_id TEXT NOT NULL REFERENCES cc_modules(module_id),
    module_params JSONB DEFAULT '{}',

    UNIQUE(variant_id, module_kind)
);

CREATE INDEX IF NOT EXISTS idx_cc_variant_modules_variant ON cc_variant_modules(variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: EXPERIMENT RUNS
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_experiment_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Variants being compared (3-way: baseline + champion + challenger)
    baseline_variant_id TEXT REFERENCES cc_variants(variant_id),  -- Always runs, can be NULL initially
    champion_variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    challenger_variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),

    -- Frozen module maps at run start (prevents mid-run changes)
    baseline_module_map_snapshot JSONB,
    champion_module_map_snapshot JSONB NOT NULL,
    challenger_module_map_snapshot JSONB NOT NULL,

    -- Run mode
    mode TEXT NOT NULL DEFAULT 'SHADOW'
        CHECK (mode IN ('SHADOW', 'LIVE_SMALL', 'SPLIT')),
    split_baseline_pct FLOAT,                 -- e.g., 10.0 for 10% to baseline
    split_champion_pct FLOAT,                 -- e.g., 60.0 for 60% to champion
    split_challenger_pct FLOAT,               -- e.g., 30.0 for 30% to challenger

    -- Scope
    setup_id TEXT NOT NULL,
    asset_universe_snapshot JSONB,            -- Frozen list of assets
    timeframes TEXT[] NOT NULL,

    -- Execution spec (frozen from paper trading defaults)
    execution_spec_snapshot JSONB NOT NULL,   -- Slippage, fees, fill model

    -- Status
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'COMPLETED', 'CANCELLED', 'PROMOTED')),

    -- Timestamps
    start_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    end_ts TIMESTAMPTZ,

    -- Notes
    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cc_runs_status ON cc_experiment_runs(status, start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_cc_runs_setup ON cc_experiment_runs(setup_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: AGGREGATED METRICS
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_run_metrics (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),

    -- Core metrics
    trade_count INT NOT NULL DEFAULT 0,
    win_count INT NOT NULL DEFAULT 0,
    loss_count INT NOT NULL DEFAULT 0,

    -- Expectancy metrics
    expectancy FLOAT,                         -- Avg R per trade
    total_r FLOAT DEFAULT 0,
    avg_r FLOAT,

    -- PnL
    gross_pnl FLOAT DEFAULT 0,
    net_pnl FLOAT DEFAULT 0,
    total_fees FLOAT DEFAULT 0,

    -- Risk metrics
    max_drawdown_pct FLOAT,
    win_rate FLOAT,
    profit_factor FLOAT,

    -- Timestamps
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(run_id, variant_id)
);

CREATE INDEX IF NOT EXISTS idx_cc_metrics_run ON cc_run_metrics(run_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: PER-ASSET METRICS (for generalization gate)
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_run_metrics_by_asset (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    symbol TEXT NOT NULL,

    -- Metrics (same as aggregated)
    trade_count INT NOT NULL DEFAULT 0,
    expectancy FLOAT,
    total_r FLOAT DEFAULT 0,
    win_rate FLOAT,
    max_drawdown_pct FLOAT,

    UNIQUE(run_id, variant_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_cc_metrics_asset ON cc_run_metrics_by_asset(run_id, variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: EXPERIMENT TRADES
-- =============================================================================

-- NOTE: Using TEXT for trade_id to match Python str(uuid.uuid4()) pattern.
-- This avoids type casting issues between Postgres UUID and Python strings.
-- Consistent with approach used elsewhere in MCAS codebase.

CREATE TABLE IF NOT EXISTS cc_experiment_trades (
    id TEXT PRIMARY KEY,                      -- str(uuid.uuid4()) from Python
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),

    -- Trade identity
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_ts TIMESTAMPTZ NOT NULL,
    signal_bar_ts TIMESTAMPTZ,                -- Bar that produced the signal

    -- Entry
    entry_ts TIMESTAMPTZ,
    entry_price FLOAT,
    entry_price_with_slippage FLOAT,

    -- Exit
    exit_ts TIMESTAMPTZ,
    exit_price FLOAT,
    exit_price_with_slippage FLOAT,
    exit_reason TEXT,

    -- Results
    r_multiple FLOAT,
    pnl_gross FLOAT,
    pnl_net FLOAT,
    fees_paid FLOAT,
    bars_held INT,
    mae FLOAT,                                -- Max adverse excursion
    mfe FLOAT,                                -- Max favorable excursion

    -- For live/split modes
    was_executed BOOLEAN DEFAULT FALSE,
    execution_id TEXT,                        -- Links to paper_positions or run_trades

    -- Audit
    trace_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cc_trades_run ON cc_experiment_trades(run_id, variant_id);
CREATE INDEX IF NOT EXISTS idx_cc_trades_symbol ON cc_experiment_trades(symbol, timeframe, signal_ts);


-- =============================================================================
-- CHAMPION/CHALLENGER: TRADE DIAGNOSTICS (v6: wins AND losses)
-- =============================================================================

CREATE TABLE IF NOT EXISTS cc_trade_diagnostics (
    id SERIAL PRIMARY KEY,
    trade_id TEXT REFERENCES cc_experiment_trades(id) ON DELETE CASCADE,  -- TEXT to match cc_experiment_trades.id
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    run_id UUID REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,

    -- Trade outcome
    is_winner BOOLEAN NOT NULL,

    -- Classification (v6: separate buckets for wins and losses)
    -- Loser buckets (when is_winner = false)
    failure_bucket TEXT
        CHECK (failure_bucket IS NULL OR failure_bucket IN (
            'BAD_REGIME',           -- Wrong market conditions
            'BAD_ENTRY_TIMING',     -- Entry too early/late
            'BAD_STOP',             -- Stop too tight/loose
            'BAD_TP_TRAILING',      -- Exit too early/late
            'LIQUIDITY_ISSUE',      -- Slippage, gap, thin book
            'SIGNAL_CONFLICT',      -- Conflicting signals
            'OTHER'
        )),

    -- Winner buckets (when is_winner = true) - v6 addition
    winner_bucket TEXT
        CHECK (winner_bucket IS NULL OR winner_bucket IN (
            'EARLY_EXIT',              -- Exited before full move captured
            'REGIME_TAILWIND',         -- Won due to favorable regime
            'ENTRY_PRECISION',         -- Entry timing was optimal
            'STOP_NEVER_THREATENED',   -- Stop was never close to being hit
            'HIGH_SLIPPAGE_WIN',       -- Won despite significant slippage
            'CONFLICTING_SIGNAL_WIN',  -- Won despite conflicting signals
            'CLEAN_WIN'                -- Textbook execution
        )),

    -- Constraint: exactly one bucket must be set based on is_winner
    CONSTRAINT chk_bucket_matches_outcome CHECK (
        (is_winner = true AND winner_bucket IS NOT NULL AND failure_bucket IS NULL) OR
        (is_winner = false AND failure_bucket IS NOT NULL AND winner_bucket IS NULL)
    ),

    -- Details
    notes TEXT,
    hypothesis_module_kind TEXT,              -- Module kind suggested by this diagnostic

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cc_diagnostics_run ON cc_trade_diagnostics(run_id, is_winner);
CREATE INDEX IF NOT EXISTS idx_cc_diagnostics_loser ON cc_trade_diagnostics(failure_bucket) WHERE is_winner = false;
CREATE INDEX IF NOT EXISTS idx_cc_diagnostics_winner ON cc_trade_diagnostics(winner_bucket) WHERE is_winner = true;


-- =============================================================================
-- CHAMPION/CHALLENGER: STATE PERSISTENCE (v7 - Redis TTL backup)
-- =============================================================================

-- Shadow positions persisted to Postgres as backup against Redis TTL expiry
CREATE TABLE IF NOT EXISTS cc_shadow_positions (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- Position state (mirrors Redis shadow position)
    trade_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_ts TIMESTAMPTZ NOT NULL,
    entry_price FLOAT NOT NULL,
    entry_price_with_slippage FLOAT NOT NULL,
    stop_price FLOAT NOT NULL,
    tp1_price FLOAT,
    r_value FLOAT NOT NULL,
    bars_held INT NOT NULL DEFAULT 0,
    half_r_touched BOOLEAN NOT NULL DEFAULT FALSE,
    mae FLOAT DEFAULT 0,
    mfe FLOAT DEFAULT 0,

    -- Pending exit (if flagged for next-bar exit)
    pending_exit_reason TEXT,
    pending_exit_flagged_at TIMESTAMPTZ,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(run_id, variant_id, symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_cc_shadow_pos_run ON cc_shadow_positions(run_id, variant_id);

-- Pending signals awaiting next-bar entry (v7 - prevents loss on Redis TTL expiry)
CREATE TABLE IF NOT EXISTS cc_pending_signals (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES cc_experiment_runs(run_id) ON DELETE CASCADE,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,

    -- Signal data
    signal_ts TIMESTAMPTZ NOT NULL,
    signal_bar_ts TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    stop_price FLOAT NOT NULL,
    tp1_price FLOAT,
    tp2_price FLOAT,
    r_value FLOAT NOT NULL,

    -- Pending age tracking (v7 - for stale signal cancellation)
    bars_pending INT NOT NULL DEFAULT 0,
    max_pending_bars INT NOT NULL DEFAULT 3,  -- Cancel if exceeds this

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(run_id, variant_id, symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_cc_pending_signals_run ON cc_pending_signals(run_id, variant_id);


-- =============================================================================
-- CHAMPION/CHALLENGER: CHANGE LEDGER (for weekly cadence enforcement)
-- =============================================================================

-- NOTE (UTC wall-clock drift): created_at uses server-generated NOW() to ensure
-- consistent UTC timestamps regardless of API client clock. All cadence checks
-- compare against DB timestamps, not client timestamps.
CREATE TABLE IF NOT EXISTS cc_change_ledger (
    id SERIAL PRIMARY KEY,
    setup_id TEXT NOT NULL,
    variant_id TEXT NOT NULL REFERENCES cc_variants(variant_id),
    change_type TEXT NOT NULL CHECK (change_type IN ('CHALLENGER_CREATED', 'PROMOTED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- Server-generated UTC
);

CREATE INDEX IF NOT EXISTS idx_cc_change_ledger_setup ON cc_change_ledger(setup_id, created_at DESC);

-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
-- To apply: psql -U trading -d trading -f this_file.sql
-- Or via Alembic: alembic upgrade head
--
-- REMINDER: This file lives in Backend/docs/db/migrations/, NOT in app code.
-- =============================================================================
```

### 4.2 Redis Keys (for live state)

**CRITICAL**: State must be namespaced by (run_id, variant_id, setup_id, symbol, tf) to prevent collision between Champion and Challenger evaluating the same stream.

```python
# =============================================================================
# Redis Key Builders (namespaced to prevent state collision)
# =============================================================================

def cc_active_run_key(setup_id: str) -> str:
    """Active run per setup (allows multiple setups to run CC independently)."""
    return f"cc:active_run:{setup_id}"  # STRING: run_id

def cc_variant_state_key(run_id: str, variant_id: str, setup_id: str, symbol: str, tf: str) -> str:
    """
    Per-symbol, per-timeframe state for a variant during an experiment.

    CRITICAL: Full namespace prevents Champion/Challenger state collision.
    Each variant maintains independent state (e.g., squeeze_age, box_high).
    """
    return f"cc:state:{run_id}:{variant_id}:{setup_id}:{symbol}:{tf}"  # HASH

def cc_shadow_position_key(run_id: str, variant_id: str, symbol: str, tf: str) -> str:
    """
    Shadow position tracking for exit simulation.

    CRITICAL: Includes timeframe to prevent collision when running CC
    on multiple timeframes (timeframes TEXT[] in cc_experiment_runs).
    """
    return f"cc:shadow_pos:{run_id}:{variant_id}:{symbol}:{tf}"  # HASH

def cc_champion_key(setup_id: str) -> str:
    """Quick lookup: current champion variant_id for a setup."""
    return f"cc:champion:{setup_id}"  # STRING

def cc_baseline_key(setup_id: str) -> str:
    """Quick lookup: baseline variant_id for a setup."""
    return f"cc:baseline:{setup_id}"  # STRING

# Cached variant objects (per run, to avoid DB chatter per bar)
def cc_variant_cache_key(run_id: str, variant_id: str) -> str:
    """Cached variant object for the lifetime of a run."""
    return f"cc:variant_cache:{run_id}:{variant_id}"  # STRING (JSON)

# TTLs
CC_STATE_TTL_S = 86400 * 14     # 14 days (2x min_run_duration to handle delays/downtime)
CC_VARIANT_CACHE_TTL_S = 86400  # 1 day (refreshed on run start)

# NOTE: State backend MUST refresh TTL on every write via EXPIRE command.
# This prevents premature expiry during long-running baseline experiments.

# =============================================================================
# v7/v8 MITIGATION: Redis TTL can silently delete state mid-run
# =============================================================================
#
# RISK: Long-lived "holding" state (shadow positions, pending signals) that goes
# quiet (no writes) for >14 days can vanish, causing phantom exits / missing trades.
# This is especially dangerous in low-activity markets / long holds.
#
# MITIGATION: Persist critical state to Postgres as backup, refresh from Redis.
#
# 1. Shadow positions persisted to `cc_shadow_positions` table (see schema)
# 2. Pending signals persisted to `cc_pending_signals` table (see schema)
# 3. On Redis miss, attempt Postgres recovery before treating as "no position"
# 4. **REQUIRED** heartbeat refresh (v8): worker MUST touch all open positions daily
#
# v8 CRITICAL: Heartbeat is REQUIRED, not "nice to have". Without it, low-activity
# markets WILL lose state silently. Schedule and monitor this task.

HEARTBEAT_INTERVAL_HOURS = 24  # REQUIRED: Run daily, not optional

def heartbeat_refresh_all_positions():
    """
    REQUIRED task: Refresh TTL on all open shadow positions.

    v8: This MUST run on a schedule (e.g., cron, celery beat, APScheduler).
    Failure to run this will cause state loss in low-activity markets.

    Schedule: Daily (every HEARTBEAT_INTERVAL_HOURS)
    Monitor: Alert if task hasn't run in 2x HEARTBEAT_INTERVAL_HOURS
    """
    active_runs = get_active_cc_runs()
    refreshed_count = 0

    for run in active_runs:
        positions = get_all_shadow_positions_from_postgres(run.run_id)
        for pos in positions:
            key = cc_shadow_position_key(run.run_id, pos.variant_id, pos.symbol, pos.timeframe)
            # Touch Redis to refresh TTL
            if redis.exists(key):
                redis.expire(key, CC_STATE_TTL_S)
                refreshed_count += 1
            else:
                # Position exists in Postgres but not Redis - recover it
                redis.hset(key, mapping=pos.to_redis_hash())
                redis.expire(key, CC_STATE_TTL_S)
                log.warn(f"Heartbeat recovered missing position: {key}")
                refreshed_count += 1

    log.info(f"Heartbeat refresh completed: {refreshed_count} positions touched")
    return refreshed_count

# Recovery flow (called on every state read):
#   state = redis.hgetall(key)
#   if not state:
#       state = postgres_recover_state(run_id, variant_id, symbol, tf)
#       if state:
#           redis.hset(key, mapping=state)
#           redis.expire(key, CC_STATE_TTL_S)
#           log.warn(f"Recovered state from Postgres: {key}")


# =============================================================================
# v8: ID Validation - Forbid ":" in identifiers
# =============================================================================
#
# RISK: If setup_id/variant_id/symbol/tf contains a colon, the key parsing
# invariant in _assert_namespace() breaks, causing false-passes or false-fails.
#
# MITIGATION: Validate and reject colons at creation time for all identifiers.

FORBIDDEN_ID_CHARS = frozenset([":"])  # Colon breaks key parsing

def validate_identifier(value: str, field_name: str) -> str:
    """
    Validate that an identifier doesn't contain forbidden characters.

    v8: Called at creation time for setup_id, variant_id, symbol, timeframe.

    Args:
        value: The identifier value to validate
        field_name: Name of the field (for error messages)

    Returns:
        The validated value (unchanged)

    Raises:
        ValueError: If identifier contains forbidden characters
    """
    if not value:
        raise ValueError(f"{field_name} cannot be empty")

    found_forbidden = FORBIDDEN_ID_CHARS & set(value)
    if found_forbidden:
        raise ValueError(
            f"{field_name} '{value}' contains forbidden character(s): {found_forbidden}. "
            f"Colons are not allowed because they're used as key delimiters."
        )

    return value


def validate_all_identifiers(
    setup_id: str = None,
    variant_id: str = None,
    symbol: str = None,
    timeframe: str = None,
):
    """
    Validate all provided identifiers.

    v8: Call this at variant creation, run creation, and signal recording.
    """
    if setup_id is not None:
        validate_identifier(setup_id, "setup_id")
    if variant_id is not None:
        validate_identifier(variant_id, "variant_id")
    if symbol is not None:
        validate_identifier(symbol, "symbol")
    if timeframe is not None:
        validate_identifier(timeframe, "timeframe")


# =============================================================================
# State Backend with Namespace Enforcement (v6 fix + v8 colon validation)
# =============================================================================

class VariantStateBackend:
    """
    State backend with mandatory namespace enforcement.

    INVARIANT: All keys MUST include {run_id}:{variant_id}:{symbol}:{tf}
    to prevent state collision between variants.
    """

    # Key format: cc:state:{run_id}:{variant_id}:{setup_id}:{symbol}:{tf}
    # Field positions (0-indexed after splitting on ':')
    KEY_PREFIX_POS = 0      # "cc"
    KEY_TYPE_POS = 1        # "state"
    RUN_ID_POS = 2
    VARIANT_ID_POS = 3
    SETUP_ID_POS = 4
    SYMBOL_POS = 5
    TF_POS = 6
    EXPECTED_FIELD_COUNT = 7

    def __init__(self, redis_client):
        self.redis = redis_client

    def _assert_namespace(self, key: str, run_id: str, variant_id: str, symbol: str, tf: str):
        """
        GUARD: Refuse reads/writes unless key has correct namespace fields at exact positions.

        v6 fix: Parse key by splitting on ':' and compare exact field positions.
        Substring-based checks can false-pass (e.g., "BTC" appears elsewhere)
        or false-fail if formatting changes.
        """
        parts = key.split(":")

        # Check field count
        if len(parts) < self.EXPECTED_FIELD_COUNT:
            raise ValueError(
                f"Namespace violation: key '{key}' has {len(parts)} fields, "
                f"expected {self.EXPECTED_FIELD_COUNT}"
            )

        # Check exact field positions
        errors = []
        if parts[self.RUN_ID_POS] != run_id:
            errors.append(f"run_id at pos {self.RUN_ID_POS}: got '{parts[self.RUN_ID_POS]}', expected '{run_id}'")
        if parts[self.VARIANT_ID_POS] != variant_id:
            errors.append(f"variant_id at pos {self.VARIANT_ID_POS}: got '{parts[self.VARIANT_ID_POS]}', expected '{variant_id}'")
        if parts[self.SYMBOL_POS] != symbol:
            errors.append(f"symbol at pos {self.SYMBOL_POS}: got '{parts[self.SYMBOL_POS]}', expected '{symbol}'")
        if parts[self.TF_POS] != tf:
            errors.append(f"tf at pos {self.TF_POS}: got '{parts[self.TF_POS]}', expected '{tf}'")

        if errors:
            raise ValueError(
                f"Namespace violation in key '{key}':\n  " + "\n  ".join(errors)
            )

    def load(self, run_id: str, variant_id: str, setup_id: str, symbol: str, tf: str) -> dict:
        """Load variant state with namespace enforcement."""
        key = cc_variant_state_key(run_id, variant_id, setup_id, symbol, tf)
        self._assert_namespace(key, run_id, variant_id, symbol, tf)
        return self.redis.hgetall(key) or {}

    def save(self, run_id: str, variant_id: str, setup_id: str, symbol: str, tf: str, state: dict):
        """Save variant state with namespace enforcement and TTL refresh."""
        key = cc_variant_state_key(run_id, variant_id, setup_id, symbol, tf)
        self._assert_namespace(key, run_id, variant_id, symbol, tf)

        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=state)
        pipe.expire(key, CC_STATE_TTL_S)  # Refresh TTL on every write
        pipe.execute()
```

---

## 5) One-Module-Different Enforcement

### 5.1 Creating a Challenger

```python
def create_challenger_from_champion(
    champion_variant_id: str,
    diff_type: str,              # "MODULE" | "PARAM_BUNDLE" | "SCORE_COMPONENT"
    diff_module_kind: str,       # "FILTER" | "REGIME" | "ENTRY" | "STOP" | "EXIT" | "SIZING" | "SCORE"
    diff_key: str,               # Descriptive: "EXIT.trailing"
    new_module_id: str = None,   # For MODULE type
    new_params: dict = None,     # For PARAM_BUNDLE or SCORE_COMPONENT type
) -> str:
    """
    Create a Challenger from a Champion.

    Enforces:
    1. One-module-different rule (exactly 1 diff)
    2. Weekly cadence (max 1 change per week per setup)
    3. No existing active challenger for this setup

    Returns:
        New challenger variant_id

    Raises:
        ValueError: If rules violated
        CadenceViolation: If <7 days since last change
        ActiveChallengerExists: If setup already has active challenger
    """
    setup_id = get_variant_setup(champion_variant_id)

    # ==========================================================================
    # Enforcement 1: Weekly cadence check
    #
    # v6 fix: Use total_seconds() instead of .days to avoid truncation issues.
    # .days truncates, so 6d23h reads as 6 days → confusing "wait 1 more day" messages.
    #
    # NOTE (UTC wall-clock drift): last_change.created_at is server-generated
    # (Postgres NOW()), stored in UTC. We compare against datetime.now(timezone.utc)
    # to avoid clock drift issues. If API servers have clock skew, use DB-side
    # comparison instead: SELECT * FROM cc_change_ledger WHERE created_at > NOW() - INTERVAL '7 days'
    # ==========================================================================
    CADENCE_SECONDS = 7 * 24 * 60 * 60  # 7 days in seconds

    last_change = get_last_change(setup_id)  # From cc_change_ledger
    if last_change:
        elapsed = datetime.now(timezone.utc) - last_change.created_at
        seconds_since = elapsed.total_seconds()

        if seconds_since < CADENCE_SECONDS:
            remaining_seconds = CADENCE_SECONDS - seconds_since
            remaining_hours = remaining_seconds / 3600
            remaining_days = remaining_hours / 24

            # Format remaining time appropriately
            if remaining_hours < 24:
                wait_msg = f"{remaining_hours:.1f} hours"
            else:
                wait_msg = f"{remaining_days:.1f} days"

            raise CadenceViolation(
                f"Weekly cadence violated: last change was {seconds_since / 86400:.1f} days ago "
                f"(variant {last_change.variant_id}). Wait {wait_msg}."
            )

    # ==========================================================================
    # Enforcement 2: No existing active challenger
    # ==========================================================================
    active_challenger = get_active_challenger(setup_id)
    if active_challenger:
        raise ActiveChallengerExists(
            f"Setup {setup_id} already has active challenger: {active_challenger.variant_id}"
        )

    # ==========================================================================
    # Load Champion's module map
    # ==========================================================================
    champion_modules = load_variant_modules(champion_variant_id)

    # ==========================================================================
    # Apply the single change
    # ==========================================================================
    challenger_modules = deepcopy(champion_modules)

    if diff_module_kind not in challenger_modules:
        raise ValueError(f"Module kind {diff_module_kind} not in Champion")

    if diff_type == "MODULE":
        # Swap the module_id at this slot
        challenger_modules[diff_module_kind].module_id = new_module_id

    elif diff_type == "PARAM_BUNDLE":
        # Update params for this module (entire param change = 1 "module change")
        challenger_modules[diff_module_kind].params = new_params

    elif diff_type == "SCORE_COMPONENT":
        # For SCORE module kind, update scoring weights
        if diff_module_kind != "SCORE":
            raise ValueError("SCORE_COMPONENT diff_type requires diff_module_kind=SCORE")
        challenger_modules["SCORE"].params.update(new_params)

    # ==========================================================================
    # Enforcement 3: Validate exactly one difference
    # ==========================================================================
    diff_count = count_differences(champion_modules, challenger_modules)
    if diff_count != 1:
        raise ValueError(f"One-module-different rule violated: {diff_count} differences")

    # ==========================================================================
    # Persist new variant
    # ==========================================================================
    version = increment_version(champion_variant_id)
    variant_id = f"{setup_id}@{version}"

    save_variant(
        variant_id=variant_id,
        setup_id=setup_id,
        status="CHALLENGER",
        parent_variant_id=champion_variant_id,
        diff_type=diff_type,
        diff_module_kind=diff_module_kind,
        diff_key=diff_key,
        modules=challenger_modules,
    )

    # Record in change ledger
    record_change(setup_id, variant_id, "CHALLENGER_CREATED")

    return variant_id
```

### 5.2 Diff Counting

```python
def count_differences(champion_modules: dict, challenger_modules: dict) -> int:
    """
    Count module differences between Champion and Challenger.

    Rules:
    - Different module_id = 1 diff
    - Same module_id but different params = 1 diff
    - Missing module = 1 diff
    """
    diff_count = 0

    all_kinds = set(champion_modules.keys()) | set(challenger_modules.keys())

    for kind in all_kinds:
        champ = champion_modules.get(kind)
        chall = challenger_modules.get(kind)

        if champ is None or chall is None:
            diff_count += 1
        elif champ.module_id != chall.module_id:
            diff_count += 1
        elif champ.params != chall.params:
            diff_count += 1

    return diff_count
```

---

## 6) Parallel Evaluation

### 6.1 Shadow Mode with 3-Way Comparison

All three variants (Baseline + Champion + Challenger) evaluate the same market stream. All record what they WOULD have done. Baseline runs forever for reference.

```python
class ParallelEvaluator:
    """
    Evaluates Baseline + Champion + Challenger on the same data stream.

    CRITICAL: Each variant has its own:
    - Namespaced state (via state_backend)
    - Dedicated ShadowTradeSimulator instance (prevents signal/exit collision)
    """

    def __init__(
        self,
        run_id: str,
        baseline: Optional[CachedVariant],
        champion: CachedVariant,
        challenger: CachedVariant,
        execution_spec: ExecutionSpec,
        state_backend: VariantStateBackend,
    ):
        self.run_id = run_id
        self.execution_spec = execution_spec
        self.state_backend = state_backend  # Auto-namespaces by variant

        # CRITICAL: One simulator PER VARIANT to prevent signal/exit collision
        # If shared, first variant to call process_pending_entries() would pop
        # the signal, and other variants would never enter.
        self.variants: dict[str, tuple[CachedVariant, ShadowTradeSimulator]] = {}

        if baseline:
            self.variants["baseline"] = (baseline, ShadowTradeSimulator(execution_spec))
        self.variants["champion"] = (champion, ShadowTradeSimulator(execution_spec))
        self.variants["challenger"] = (challenger, ShadowTradeSimulator(execution_spec))

    def evaluate_bar(self, symbol: str, timeframe: str, snapshot: IndicatorSnapshot):
        """
        Evaluate all variants on the same bar.

        Each produces independent:
        - State update (namespaced)
        - Signal (or None)
        - Trade outcome simulation (if signal active)

        Order of operations per variant:
        1. Process pending exits (TIME_STOP at next bar open)
        2. Process pending entries (signal from previous bar)
        3. Compute new state
        4. Evaluate trigger (may record new signal for next bar)
        5. Check exits on open positions
        """
        for role, (variant, simulator) in self.variants.items():
            # Load variant-specific state (namespaced key)
            state = self.state_backend.load(
                run_id=self.run_id,
                variant_id=variant.variant_id,
                setup_id=variant.setup_id,
                symbol=symbol,
                tf=timeframe,
            )

            # ===================================================================
            # Step 1: Process pending exits (TIME_STOP finalization at bar open)
            # ===================================================================
            self._process_pending_exits(variant, simulator, symbol, timeframe, snapshot)

            # ===================================================================
            # Step 2: Process pending entries (next-bar-open entry)
            # ===================================================================
            new_trade = simulator.process_pending_entries(symbol, timeframe, snapshot)
            if new_trade:
                save_cc_trade(self.run_id, variant.variant_id, new_trade)
                save_shadow_position(self.run_id, variant.variant_id, symbol, timeframe, new_trade)

            # ===================================================================
            # Step 3: Compute new state
            # ===================================================================
            new_state = variant.evaluator.compute_state(snapshot, prev_state=state)

            # ===================================================================
            # Step 4: Evaluate trigger
            # ===================================================================
            signal = variant.evaluator.evaluate_trigger(
                snapshot, new_state, symbol, timeframe
            )

            # Save updated state (namespaced)
            self.state_backend.save(
                run_id=self.run_id,
                variant_id=variant.variant_id,
                setup_id=variant.setup_id,
                symbol=symbol,
                tf=timeframe,
                state=new_state,
            )

            # Record signal for next-bar entry (if triggered)
            if signal:
                simulator.record_signal(signal, symbol, timeframe)
            else:
                # v7: Age any existing pending signal that wasn't processed this bar
                simulator.age_pending_signals(symbol, timeframe)

            # ===================================================================
            # Step 5: Check exits for any open shadow positions
            # ===================================================================
            self._check_shadow_exits(variant, simulator, symbol, timeframe, snapshot)

    def _process_pending_exits(self, variant, simulator, symbol: str, tf: str, snapshot):
        """
        Finalize TIME_STOP exits at this bar's OPEN.

        Called at START of bar processing, before entries.

        v7 MITIGATION: If pending exit has been waiting > MAX_PENDING_BARS,
        force-resolve at current bar's open with warning.
        """
        shadow_pos = load_shadow_position(self.run_id, variant.variant_id, symbol, tf)
        if not shadow_pos or not shadow_pos.pending_exit:
            return

        bar = snapshot.ohlcv[-1]
        pending = shadow_pos.pending_exit

        # v7: Track and warn on stale pending exits
        bars_pending = getattr(pending, 'bars_pending', 0) + 1
        if bars_pending > simulator.MAX_PENDING_BARS:
            log.warn(
                f"Force-resolving stale pending exit for {symbol}:{tf} - "
                f"pending for {bars_pending} bars (max {simulator.MAX_PENDING_BARS}). "
                f"Likely data gap. Using current bar's open."
            )

        # Exit at this bar's OPEN (not previous bar's close)
        exit_price = bar.open
        exit_with_slippage = apply_slippage(  # REUSE from execution.py
            exit_price,
            shadow_pos.direction,
            self.execution_spec.slippage_exit_bps,
        )

        # Calculate fees using shared execution model
        fees = calculate_fees(  # REUSE from execution.py
            notional=exit_with_slippage * shadow_pos.position_size,
            is_maker=False,  # TIME_STOP is market order = taker
            fee_maker_bps=self.execution_spec.fee_maker_bps,
            fee_taker_bps=self.execution_spec.fee_taker_bps,
        )

        # Finalize with correct exit price
        finalize_shadow_trade(
            run_id=self.run_id,
            variant_id=variant.variant_id,
            trade_id=shadow_pos.trade_id,
            exit_reason=pending.exit_reason,
            exit_price=exit_price,
            exit_price_with_slippage=exit_with_slippage,
            exit_ts=bar.open_time,
            fees=fees,
        )
        clear_shadow_position(self.run_id, variant.variant_id, symbol, tf)

    def _check_shadow_exits(self, variant, simulator, symbol: str, tf: str, snapshot):
        """Simulate bar-by-bar exit checks using same logic as paper trading."""
        # Key includes tf to prevent collision on multi-TF runs
        shadow_pos = load_shadow_position(self.run_id, variant.variant_id, symbol, tf)
        if not shadow_pos or shadow_pos.pending_exit:
            return  # Already pending exit, will process at next bar open

        exit_result = simulator.check_exit(
            position=shadow_pos,
            snapshot=snapshot,
        )

        if exit_result.should_exit:
            if exit_result.exit_at_next_open:
                # TIME_STOP: mark for exit at next bar open
                shadow_pos.pending_exit = exit_result
                save_shadow_position(self.run_id, variant.variant_id, symbol, tf, shadow_pos)
            else:
                # Immediate exit (STOP_LOSS, TP1) - "touch model" means exit happened
                # somewhere during the bar. Use bar.close_time to match paper_trading.py
                # semantics (we don't know exact intra-bar time, so use bar close).
                bar = snapshot.ohlcv[-1]
                exit_with_slippage = apply_slippage(
                    exit_result.exit_price,
                    shadow_pos.direction,
                    self.execution_spec.slippage_exit_bps,
                )
                fees = calculate_fees(
                    notional=exit_with_slippage * shadow_pos.position_size,
                    is_maker=False,
                    fee_maker_bps=self.execution_spec.fee_maker_bps,
                    fee_taker_bps=self.execution_spec.fee_taker_bps,
                )
                finalize_shadow_trade(
                    run_id=self.run_id,
                    variant_id=variant.variant_id,
                    trade_id=shadow_pos.trade_id,
                    exit_reason=exit_result.exit_reason,
                    exit_price=exit_result.exit_price,
                    exit_price_with_slippage=exit_with_slippage,
                    exit_ts=bar.close_time,  # Touch model: use bar close (matches paper_trading.py)
                    fees=fees,
                )
                clear_shadow_position(self.run_id, variant.variant_id, symbol, tf)
```

### 6.2 Shadow Trade Simulator

**CRITICAL REQUIREMENT**: `ShadowTradeSimulator` MUST call the same functions/classes used by `paper_trading.py` and `run_executor.py` for fills, fees, and slippage. Do not reimplement - import and reuse.

**v7 MITIGATION: Execution Context Isolation**

Reusing execution functions is correct but risks side-effects if those functions mutate shared state or publish to live queues.

```python
# =============================================================================
# Execution Context Flag (v7 - prevents shadow runs from enqueuing real orders)
# =============================================================================

class ExecutionContext(Enum):
    """
    Execution context flag passed to all execution functions.

    INVARIANT: Functions MUST check this flag before any:
    - Queue publish (order submission)
    - External API call (exchange)
    - Shared state mutation
    """
    LIVE = "live"           # Real execution - can publish orders
    PAPER = "paper"         # Paper trading - writes to paper tables only
    SHADOW = "shadow"       # CC shadow - NO external effects, metrics only


def assert_shadow_safe(context: ExecutionContext, operation: str):
    """
    Guard: Raise if attempting forbidden operation in SHADOW context.

    Call this at the top of any function that could have side effects.
    """
    if context == ExecutionContext.SHADOW:
        raise RuntimeError(
            f"SHADOW context violation: attempted '{operation}' in shadow mode. "
            f"Shadow runs MUST NOT have external effects."
        )


# Example usage in execution.py:
def submit_order(order: Order, context: ExecutionContext = ExecutionContext.LIVE):
    assert_shadow_safe(context, "submit_order")
    # ... actual order submission

def publish_to_queue(msg: dict, context: ExecutionContext = ExecutionContext.LIVE):
    assert_shadow_safe(context, "publish_to_queue")
    # ... actual queue publish
```

**Required tests** (acceptance criteria):
- Test that `apply_slippage()` works in SHADOW context (pure math, no side effects)
- Test that `calculate_fees()` works in SHADOW context (pure math)
- Test that `submit_order()` raises in SHADOW context
- Test that any queue publish raises in SHADOW context

```python
class ShadowTradeSimulator:
    """
    Simulates trade outcomes using the SAME execution model as paper trading.

    MUST reuse:
    - execution.apply_slippage() for slippage calculation
    - execution.calculate_fees() for fee calculation
    - paper_trading.check_exit_conditions() for exit logic (or equivalent)

    This ensures CC metrics are comparable to real paper trading outcomes.

    v7: All calls pass ExecutionContext.SHADOW to prevent side effects.
    """

    # v7: Max bars a pending signal/exit can wait before being cancelled
    MAX_PENDING_BARS = 3

    def __init__(self, execution_spec: ExecutionSpec):
        self.spec = execution_spec
        # Pending signals awaiting next-bar entry
        # v7: Track bars_pending for stale signal cancellation
        self._pending_signals: dict[str, tuple[Signal, int]] = {}  # key: symbol:tf -> (signal, bars_pending)

    def record_signal(self, signal: Signal, symbol: str, tf: str):
        """
        Record a signal for next-bar entry.

        MCAS canonical entry is NEXT_OPEN: signal on bar N, enter at bar N+1 open.
        Store the signal and process entry on the next evaluate_bar() call.
        """
        key = f"{symbol}:{tf}"
        self._pending_signals[key] = (signal, 0)  # 0 bars pending initially

    def process_pending_entries(self, symbol: str, tf: str, snapshot: IndicatorSnapshot) -> Optional[ShadowTrade]:
        """
        Called at the START of each bar to process pending signals.

        Entry price: Current bar's OPEN + slippage (not previous bar's close).

        v7 MITIGATION: If signal has been pending > MAX_PENDING_BARS (data gap),
        cancel it and log a warning instead of entering stale.
        """
        key = f"{symbol}:{tf}"
        pending = self._pending_signals.pop(key, None)
        if not pending:
            return None

        signal, bars_pending = pending

        # v7: Check for stale signal (data gap protection)
        if bars_pending >= self.MAX_PENDING_BARS:
            log.warn(
                f"Cancelled stale pending signal for {symbol}:{tf} - "
                f"pending for {bars_pending} bars (max {self.MAX_PENDING_BARS}). "
                f"Likely data gap."
            )
            # Record as cancelled trade for metrics visibility
            return ShadowTrade(
                trade_id=str(uuid.uuid4()),
                signal_ts=signal.signal_ts,
                signal_bar_ts=signal.bar_start_ts,
                entry_ts=None,  # Never entered
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                direction=signal.direction,
                entry_price=None,
                exit_reason="CANCELLED_STALE_SIGNAL",
                r_multiple=0.0,
                bars_held=0,
            )

        bar = snapshot.ohlcv[-1]

        # Entry at this bar's OPEN (next bar after signal)
        entry_price = bar.open
        entry_with_slippage = apply_slippage(  # REUSE from execution.py
            entry_price,
            signal.direction,
            self.spec.slippage_entry_bps,
        )

        return ShadowTrade(
            trade_id=str(uuid.uuid4()),
            signal_ts=signal.signal_ts,
            signal_bar_ts=signal.bar_start_ts,
            entry_ts=bar.open_time,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            direction=signal.direction,
            entry_price=entry_price,
            entry_price_with_slippage=entry_with_slippage,
            stop_price=signal.stop_price,
            tp1_price=signal.tp1_price,
            tp2_price=signal.tp2_price,
            r_value=signal.r_value,
            bars_held=0,
            high_water_mark=entry_with_slippage,
            half_r_touched=False,
            mae=0.0,
            mfe=0.0,
        )

    def check_exit(self, position: ShadowTrade, snapshot: IndicatorSnapshot) -> ExitResult:
        """
        Check exit conditions using SAME priority as paper trading.

        MVP implements: STOP_LOSS → TIME_STOP → TP1
        Full parity (future): STOP_LOSS → FAIL_FAST → TIME_STOP → TRAILING_STOP → TP1 → TP2

        NOTE: FAIL_FAST and TRAILING_STOP are not implemented in MVP.
        Document clearly if adding them later.
        """
        bar = snapshot.ohlcv[-1]
        position.bars_held += 1

        # Update MAE/MFE (LONG and SHORT symmetric)
        if position.direction == Direction.LONG:
            position.mae = min(position.mae, bar.low - position.entry_price_with_slippage)
            position.mfe = max(position.mfe, bar.high - position.entry_price_with_slippage)
        else:  # SHORT
            position.mae = min(position.mae, position.entry_price_with_slippage - bar.high)
            position.mfe = max(position.mfe, position.entry_price_with_slippage - bar.low)

        # Track 0.5R progress for time stop (BOTH directions)
        if position.direction == Direction.LONG:
            half_r_target = position.entry_price_with_slippage + 0.5 * position.r_value
            if bar.high >= half_r_target:
                position.half_r_touched = True
        else:  # SHORT
            half_r_target = position.entry_price_with_slippage - 0.5 * position.r_value
            if bar.low <= half_r_target:
                position.half_r_touched = True

        # =====================================================================
        # Exit checks (SAME priority as paper_trading.py)
        # =====================================================================

        # 1. Stop loss (touch model)
        if self._hit_stop(position, bar):
            return ExitResult(
                should_exit=True,
                exit_reason="STOP_LOSS",
                exit_price=position.stop_price,
            )

        # 2. Time stop (Canon: progress-to-0.5R within N_dead bars, exit at NEXT BAR OPEN)
        #    Window check: bars_held >= time_stop_bars (not >)
        #    Exit price: NEXT bar open, so we flag it and exit on next call
        time_stop_bars = get_time_stop_bars(position.timeframe)
        if position.bars_held >= time_stop_bars and not position.half_r_touched:
            # Flag for exit at next bar open
            return ExitResult(
                should_exit=True,
                exit_reason="TIME_STOP",
                exit_price=bar.close,  # Placeholder; actual exit at next bar open
                exit_at_next_open=True,  # Signal to caller to use next bar's open
            )

        # 3. TP1 (touch model)
        if self._hit_tp1(position, bar):
            return ExitResult(
                should_exit=True,
                exit_reason="TP1",
                exit_price=position.tp1_price,
            )

        return ExitResult(should_exit=False)

    def _hit_stop(self, position: ShadowTrade, bar) -> bool:
        """Check if stop was hit (touch model, same as paper_trading.py)."""
        if position.direction == Direction.LONG:
            return bar.low <= position.stop_price
        else:  # SHORT
            return bar.high >= position.stop_price

    def _hit_tp1(self, position: ShadowTrade, bar) -> bool:
        """Check if TP1 was hit (touch model, same as paper_trading.py)."""
        if position.direction == Direction.LONG:
            return bar.high >= position.tp1_price
        else:  # SHORT
            return bar.low <= position.tp1_price

    def age_pending_signals(self, symbol: str, tf: str):
        """
        v7: Increment bars_pending for any signal not processed this bar.

        Called when a new bar arrives but the pending signal wasn't consumed
        (e.g., a new signal was recorded instead, or bar was processed for
        a different reason). Prevents signals from waiting indefinitely on
        data gaps.
        """
        key = f"{symbol}:{tf}"
        if key in self._pending_signals:
            signal, bars_pending = self._pending_signals[key]
            self._pending_signals[key] = (signal, bars_pending + 1)

            if bars_pending + 1 >= self.MAX_PENDING_BARS:
                log.warn(
                    f"Pending signal for {symbol}:{tf} aged to {bars_pending + 1} bars - "
                    f"will be cancelled on next process_pending_entries() call"
                )
```

### 6.3 Integration with Signal Worker (Option B: Baseline Always Runs)

**v6 (canon-compliant)**: Baseline ALWAYS evaluates, even when no CC experiment is active. This ensures baseline metrics accumulate continuously for comparison.

```python
# In signal_worker.py

def _evaluate_symbol(self, symbol: str, tf: str, snapshot: IndicatorSnapshot):
    """
    Modified to support C/C parallel evaluation with baseline always running.

    v6 (Option B implementation):
    - Baseline ALWAYS evaluates alongside champion/normal (even without CC run)
    - If CC active for setup X: Run 3-way (baseline + champ + chall)
    - If no CC active: Run 2-way (baseline + champion/normal)
    """
    for setup_id in self.active_setup_ids:  # ["S1", "S2", "U1"]
        cc_run = self._get_active_cc_run(setup_id)

        if cc_run:
            # CC experiment active: 3-way parallel (baseline + champ + chall)
            self._evaluate_cc_parallel(cc_run, symbol, tf, snapshot)
        else:
            # No CC experiment: 2-way parallel (baseline + champion)
            # This implements "baseline runs forever" per canon
            self._evaluate_with_baseline(setup_id, symbol, tf, snapshot)


def _evaluate_with_baseline(self, setup_id: str, symbol: str, tf: str, snapshot: IndicatorSnapshot):
    """
    2-way evaluation: Baseline + Champion, even when no CC experiment is active.

    v6 addition (canon compliance): Ensures baseline metrics accumulate
    continuously, so when a new CC experiment starts, baseline data is fresh.
    """
    baseline_variant_id = r.get(cc_baseline_key(setup_id))
    champion_variant_id = r.get(cc_champion_key(setup_id))

    if not baseline_variant_id:
        # No baseline defined for this setup - fall back to normal eval
        self._evaluate_normal(setup_id, symbol, tf, snapshot)
        return

    # Get or create a "baseline-only" shadow run for metrics accumulation
    evaluator = self._get_or_create_baseline_evaluator(setup_id)

    # Evaluate both baseline and champion (2-way comparison)
    evaluator.evaluate_bar(symbol, tf, snapshot)

    # Normal evaluation still happens for actual signal generation
    self._evaluate_normal(setup_id, symbol, tf, snapshot)


def _evaluate_cc_parallel(self, cc_run: ExperimentRun, symbol: str, tf: str, snapshot: IndicatorSnapshot):
    """
    Parallel 3-way evaluation for a setup under CC experiment.
    """
    # Get cached evaluator (created once per run, not per bar)
    evaluator = self._get_or_create_cc_evaluator(cc_run)

    # Evaluate all 3 variants on this bar
    evaluator.evaluate_bar(symbol, tf, snapshot)


# =============================================================================
# Cache Entry Structure (v6 fix: canonical shape to prevent mismatch bugs)
# =============================================================================

@dataclass
class CCEvaluatorCacheEntry:
    """
    Canonical cache entry structure.

    CRITICAL: All cache reads/writes MUST use this structure.
    Do NOT store raw ParallelEvaluator or tuples - use this dataclass.
    """
    evaluator: ParallelEvaluator
    last_used: float  # time.time() timestamp


def _get_or_create_cc_evaluator(self, cc_run: ExperimentRun) -> ParallelEvaluator:
    """
    Get or create cached ParallelEvaluator for this run.

    Variants are loaded ONCE at run start and cached for the run lifetime.
    Module maps are frozen in cc_experiment_runs at start.
    """
    cache_key = f"cc_evaluator:{cc_run.run_id}"
    now = time.time()

    # Check if cached and still valid
    if cache_key in self._cc_evaluator_cache:
        entry: CCEvaluatorCacheEntry = self._cc_evaluator_cache[cache_key]
        # Update last-used timestamp
        entry.last_used = now
        return entry.evaluator

    # Create new evaluator
    baseline = load_variant_from_snapshot(cc_run.baseline_module_map_snapshot) if cc_run.baseline_variant_id else None
    champion = load_variant_from_snapshot(cc_run.champion_module_map_snapshot)
    challenger = load_variant_from_snapshot(cc_run.challenger_module_map_snapshot)
    execution_spec = ExecutionSpec.from_dict(cc_run.execution_spec_snapshot)

    evaluator = ParallelEvaluator(
        run_id=cc_run.run_id,
        baseline=baseline,
        champion=champion,
        challenger=challenger,
        execution_spec=execution_spec,
        state_backend=VariantStateBackend(self.redis),
    )

    # Store using canonical cache entry structure
    self._cc_evaluator_cache[cache_key] = CCEvaluatorCacheEntry(
        evaluator=evaluator,
        last_used=now,
    )
    return evaluator


def _evict_cc_evaluator(self, run_id: str):
    """
    Explicit cache eviction on run completion/closure.

    MUST be called when a run is completed, cancelled, or promoted.
    Prevents unbounded memory growth in long-lived worker processes.
    """
    cache_key = f"cc_evaluator:{run_id}"
    if cache_key in self._cc_evaluator_cache:
        del self._cc_evaluator_cache[cache_key]
        log.info(f"Evicted CC evaluator cache for run {run_id}")


# =============================================================================
# v8: Periodic Cache Cleanup - REQUIRED Scheduling
# =============================================================================
#
# RISK: If a run dies ungracefully (worker crash, OOM, etc.), _evict_cc_evaluator()
# is never called. Without periodic cleanup, cache grows unboundedly in long-lived
# worker processes.
#
# MITIGATION: Schedule _evict_stale_cc_evaluators() and monitor it.

CACHE_CLEANUP_INTERVAL_SECONDS = 3600  # Run every hour

def _evict_stale_cc_evaluators(self, max_age_seconds: int = 86400):
    """
    TTL-based eviction fallback for stale cache entries.

    v8 REQUIRED: This MUST be scheduled to run periodically.

    Scheduling options:
    - APScheduler: scheduler.add_job(_evict_stale_cc_evaluators, 'interval', hours=1)
    - Celery Beat: @app.task; celery.conf.beat_schedule = {...}
    - Threading: threading.Timer (less reliable)
    - Worker startup: Schedule on worker init, not lazily

    Monitoring:
    - Log cache size before/after each cleanup
    - Alert if cache size grows beyond threshold (e.g., >100 entries)
    - Alert if cleanup hasn't run in 2x CACHE_CLEANUP_INTERVAL_SECONDS

    Args:
        max_age_seconds: Evict entries not used in this many seconds (default: 24h)
    """
    cache_size_before = len(self._cc_evaluator_cache)
    now = time.time()
    stale_keys = []

    for cache_key, entry in self._cc_evaluator_cache.items():
        entry: CCEvaluatorCacheEntry
        if now - entry.last_used > max_age_seconds:
            stale_keys.append(cache_key)

    for key in stale_keys:
        del self._cc_evaluator_cache[key]

    cache_size_after = len(self._cc_evaluator_cache)

    log.info(
        f"Cache cleanup: {len(stale_keys)} stale entries evicted, "
        f"cache size {cache_size_before} -> {cache_size_after}"
    )

    # v8: Alert if cache is suspiciously large
    CACHE_SIZE_ALERT_THRESHOLD = 100
    if cache_size_after > CACHE_SIZE_ALERT_THRESHOLD:
        log.warn(
            f"CC evaluator cache size ({cache_size_after}) exceeds threshold "
            f"({CACHE_SIZE_ALERT_THRESHOLD}). Possible memory leak or many active runs."
        )
```

---

## 7) Promotion Gates

### 7.1 Gate Definitions

```python
@dataclass
class PromotionConfig:
    """Configuration for promotion gates."""

    # Gate 1: Expectancy improvement
    min_expectancy_improvement_pct: float = 0.0  # Must improve (>0)

    # Gate 2: Drawdown tolerance
    max_drawdown_increase_pct: float = 5.0  # Challenger DD can be at most 5% worse

    # Gate 3: Multi-asset generalization
    min_assets_improved: int = 3            # Must beat Champion on >= 3 assets
    min_trades_per_asset: int = 5           # Each asset must have >= 5 trades
    use_median_comparison: bool = True      # Use median per-asset expectancy


# =============================================================================
# v8: Safe Metrics - No inf/NaN Propagation
# =============================================================================
#
# RISK: Metrics edge cases (division by zero, empty sets) can produce inf/NaN
# values that propagate into UI, DB, and cause display issues or crashes.
#
# DISCIPLINE: All derived percentage metrics MUST use safe_pct() or safe_ratio().

import math

def safe_pct(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Calculate percentage safely, returning default if denominator is zero/invalid.

    v8: Use this for ALL percentage calculations to prevent inf/NaN.

    Returns:
        (numerator / denominator) * 100, or default if denominator is zero/None/NaN
    """
    if denominator is None or denominator == 0 or math.isnan(denominator):
        return default
    result = (numerator / denominator) * 100
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Calculate ratio safely, returning default if denominator is zero/invalid.

    v8: Use this for ALL ratio calculations to prevent inf/NaN.
    """
    if denominator is None or denominator == 0 or math.isnan(denominator):
        return default
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def safe_improvement_pct(old_value: float, new_value: float) -> tuple[float, str]:
    """
    Calculate improvement percentage with human-readable edge case handling.

    v8: Returns (pct, description) tuple for display.

    Returns:
        (improvement_pct, description)
        - Normal case: (15.5, "15.5% improvement")
        - Old was zero, new positive: (None, "new > 0 (baseline was 0)")
        - Both zero: (0.0, "no change (both 0)")
        - Old was zero, new negative: (None, "new < 0 (baseline was 0)")
    """
    if old_value == 0:
        if new_value > 0:
            return (None, "new > 0 (baseline was 0)")
        elif new_value == 0:
            return (0.0, "no change (both 0)")
        else:
            return (None, "new < 0 (baseline was 0)")

    pct = ((new_value - old_value) / abs(old_value)) * 100

    # Guard against NaN/inf from edge cases
    if math.isnan(pct) or math.isinf(pct):
        return (None, f"calculation error ({old_value} -> {new_value})")

    return (pct, f"{pct:+.1f}% {'improvement' if pct > 0 else 'decline'}")


def check_promotion_gates(
    run_id: str,
    config: PromotionConfig = PromotionConfig(),
) -> tuple[bool, dict]:
    """
    Check if Challenger should be promoted to Champion.

    Returns:
        (should_promote, gate_results)
    """
    metrics = load_run_metrics(run_id)
    champ_metrics = metrics.champion
    chall_metrics = metrics.challenger

    gate_results = {}

    # ==========================================================================
    # Gate 1: Expectancy improves
    # ==========================================================================
    expectancy_improved = (
        chall_metrics.expectancy > champ_metrics.expectancy *
        (1 + config.min_expectancy_improvement_pct / 100)
    )

    # v8: Use safe improvement calculation (no inf/NaN propagation)
    improvement_pct, improvement_desc = safe_improvement_pct(
        champ_metrics.expectancy,
        chall_metrics.expectancy
    )

    gate_results["expectancy"] = {
        "passed": expectancy_improved,
        "champion": champ_metrics.expectancy,
        "challenger": chall_metrics.expectancy,
        "improvement_pct": improvement_pct,  # May be None for edge cases
        "improvement_desc": improvement_desc,  # Human-readable description
    }

    # ==========================================================================
    # Gate 2: Drawdown within tolerance
    # ==========================================================================
    dd_within_tolerance = (
        chall_metrics.max_drawdown_pct <=
        champ_metrics.max_drawdown_pct + config.max_drawdown_increase_pct
    )
    gate_results["drawdown"] = {
        "passed": dd_within_tolerance,
        "champion": champ_metrics.max_drawdown_pct,
        "challenger": chall_metrics.max_drawdown_pct,
        "tolerance": config.max_drawdown_increase_pct,
    }

    # ==========================================================================
    # Gate 3: Multi-asset generalization (with empty-set protection)
    #
    # CRITICAL FIX (v5): Require BOTH champion AND challenger to have
    # min_trades_per_asset for a symbol before it can:
    #   1. Count toward assets_with_enough_trades
    #   2. Contribute to assets_improved
    # This prevents "unfair wins" where challenger beats a statistically
    # meaningless champion number (e.g., champion had <5 trades on that symbol).
    # ==========================================================================
    per_asset = load_per_asset_metrics(run_id)

    # Collect assets where BOTH variants have sufficient trades
    assets_with_enough_trades = 0
    assets_improved = 0
    champ_qualifying_expectancies = []
    chall_qualifying_expectancies = []

    for symbol in per_asset.symbols:
        champ_asset = per_asset.get(champ_metrics.variant_id, symbol)
        chall_asset = per_asset.get(chall_metrics.variant_id, symbol)

        # BOTH must have min trades for fair comparison
        champ_has_enough = champ_asset.trade_count >= config.min_trades_per_asset
        chall_has_enough = chall_asset.trade_count >= config.min_trades_per_asset

        if champ_has_enough and chall_has_enough:
            # This asset qualifies for comparison
            assets_with_enough_trades += 1
            champ_qualifying_expectancies.append(champ_asset.expectancy)
            chall_qualifying_expectancies.append(chall_asset.expectancy)

            # Count improvement only for qualifying assets
            if chall_asset.expectancy > champ_asset.expectancy:
                assets_improved += 1

    # GUARD: Insufficient sample size → gate fails explicitly
    if assets_with_enough_trades < config.min_assets_improved:
        gate_results["generalization"] = {
            "passed": False,
            "reason": "INSUFFICIENT_SAMPLE_SIZE",
            "assets_with_enough_trades": assets_with_enough_trades,
            "min_required": config.min_assets_improved,
            "assets_improved": assets_improved,
        }
        return False, gate_results

    generalizes = assets_improved >= config.min_assets_improved

    # Median comparison (with empty-set guard - already covered by above check)
    if config.use_median_comparison:
        # Since assets_with_enough_trades >= min_assets_improved >= 1,
        # both lists are guaranteed non-empty at this point
        champ_median = median(champ_qualifying_expectancies)
        chall_median = median(chall_qualifying_expectancies)
        generalizes = generalizes and (chall_median > champ_median)

        gate_results["generalization"] = {
            "passed": generalizes,
            "assets_improved": assets_improved,
            "assets_with_enough_trades": assets_with_enough_trades,
            "min_required": config.min_assets_improved,
            "champ_median_expectancy": champ_median,
            "chall_median_expectancy": chall_median,
        }
    else:
        gate_results["generalization"] = {
            "passed": generalizes,
            "assets_improved": assets_improved,
            "assets_with_enough_trades": assets_with_enough_trades,
            "min_required": config.min_assets_improved,
        }

    # All gates must pass
    all_passed = expectancy_improved and dd_within_tolerance and generalizes

    return all_passed, gate_results
```

### 7.2 Promotion Execution

**v8 CRITICAL: Promotion Concurrency / Double-Promotion Race**

Without proper locking, two workers (or two API calls) attempting promotion at once can result in:
- Inconsistent variant statuses
- Overwritten champion key in Redis
- Corrupted change ledger

**Required safeguards:**
1. Wrap ALL promotion logic in a single DB transaction
2. Acquire row-level lock on the run (FOR UPDATE)
3. Enforce "one active run per setup" constraint
4. Verify run status hasn't changed before committing

```python
def execute_promotion(run_id: str) -> bool:
    """
    Promote Challenger to Champion.

    v8: Wrapped in transaction with row-level locking to prevent race conditions.

    Returns:
        True if promotion succeeded

    Raises:
        PromotionDenied: If gates don't pass
        PromotionRaceError: If run was already promoted by another process
    """
    with db.transaction() as tx:
        # =======================================================================
        # v8: Acquire row-level lock on the run to prevent concurrent promotion
        # =======================================================================
        run = tx.execute(
            """
            SELECT * FROM cc_experiment_runs
            WHERE run_id = %s
            FOR UPDATE NOWAIT  -- Fail immediately if locked by another process
            """,
            (run_id,)
        ).fetchone()

        if not run:
            raise ValueError(f"Run {run_id} not found")

        # v8: Check run hasn't already been promoted (race condition guard)
        if run.status == "PROMOTED":
            raise PromotionRaceError(
                f"Run {run_id} already promoted (likely by concurrent process)"
            )

        if run.status != "RUNNING":
            raise PromotionDenied(
                f"Run {run_id} status is {run.status}, expected RUNNING"
            )

        # Verify gates pass
        should_promote, gates = check_promotion_gates(run_id)
        if not should_promote:
            raise PromotionDenied(f"Gates not passed: {gates}")

        setup_id = get_variant_setup(run.challenger_variant_id)

        # =======================================================================
        # v8: Also lock the setup's current champion variant to prevent conflicts
        # =======================================================================
        tx.execute(
            """
            SELECT * FROM cc_variants
            WHERE variant_id = %s
            FOR UPDATE
            """,
            (run.champion_variant_id,)
        )

        # =======================================================================
        # All updates in single transaction
        # =======================================================================

        # 1. Demote old Champion to RETIRED
        tx.execute(
            "UPDATE cc_variants SET status = 'RETIRED' WHERE variant_id = %s",
            (run.champion_variant_id,)
        )

        # 2. Promote Challenger to CHAMPION
        tx.execute(
            "UPDATE cc_variants SET status = 'CHAMPION' WHERE variant_id = %s",
            (run.challenger_variant_id,)
        )

        # 3. Mark run as PROMOTED
        tx.execute(
            "UPDATE cc_experiment_runs SET status = 'PROMOTED', end_ts = NOW() WHERE run_id = %s",
            (run_id,)
        )

        # 4. Record in change ledger
        tx.execute(
            """
            INSERT INTO cc_change_ledger (setup_id, variant_id, change_type)
            VALUES (%s, %s, 'PROMOTED')
            """,
            (setup_id, run.challenger_variant_id)
        )

        # Transaction commits here

    # =======================================================================
    # v8: Update Redis AFTER transaction commits (eventual consistency is OK)
    # =======================================================================
    r.set(cc_champion_key(setup_id), run.challenger_variant_id)

    # Evict the evaluator cache for this run
    _evict_cc_evaluator(run_id)

    log.info(f"Promoted {run.challenger_variant_id} to CHAMPION for {setup_id}")

    return True


# =============================================================================
# v8: One Active Run Per Setup Enforcement
# =============================================================================

def start_experiment_run(...) -> str:
    """
    Start a new experiment run.

    v8: Enforces one active run per setup via transactional check.
    """
    with db.transaction() as tx:
        # Check for existing active run for this setup
        existing = tx.execute(
            """
            SELECT run_id FROM cc_experiment_runs
            WHERE setup_id = %s AND status = 'RUNNING'
            FOR UPDATE  -- Lock to prevent race
            """,
            (setup_id,)
        ).fetchone()

        if existing:
            raise ActiveRunExists(
                f"Setup {setup_id} already has active run: {existing.run_id}. "
                f"Close or cancel it before starting a new experiment."
            )

        # Create new run (rest of creation logic...)
        ...
```

---

## 8) Postmortem & Trade Diagnostics

**Canon requirement (v6)**: Diagnostics run on EVERY closed trade (win or loss). Winner buckets feed hypotheses but don't directly trigger changes. Changes only recommended when winner and loser buckets converge on the same module (non-confounding rule).

### 8.1 Failure Buckets (Losses)

| Bucket | Description | Suggested module_kind |
|--------|-------------|----------------------|
| BAD_REGIME | Wrong market conditions (trending when should be ranging, etc.) | REGIME |
| BAD_ENTRY_TIMING | Entry too early (stopped out) or too late (missed move) | ENTRY |
| BAD_STOP | Stop too tight (noise stop) or too loose (unnecessary loss) | STOP |
| BAD_TP_TRAILING | Took profit too early or trailed too tight | EXIT |
| LIQUIDITY_ISSUE | Slippage, gap, thin order book | FILTER |
| SIGNAL_CONFLICT | Multiple conflicting signals, wrong one taken | SCORE |
| OTHER | Unclassified | - |

### 8.2 Winner Buckets (Edge Leak Detection)

| Bucket | Description | Hypothesis module_kind |
|--------|-------------|------------------------|
| EARLY_EXIT | Exited before full move captured (MFE >> realized R) | EXIT |
| REGIME_TAILWIND | Won primarily due to favorable regime, not signal quality | REGIME |
| ENTRY_PRECISION | Entry timing was optimal (low MAE) | ENTRY |
| STOP_NEVER_THREATENED | Stop was far from ever being hit | STOP |
| HIGH_SLIPPAGE_WIN | Won despite significant slippage | FILTER |
| CONFLICTING_SIGNAL_WIN | Won despite conflicting signals | SCORE |
| CLEAN_WIN | Textbook execution, no anomalies | - |

### 8.3 Bucket Classification Logic

```python
def classify_failure(trade: CCTrade, trace: TradeTrace) -> str:
    """
    Classify a losing trade into a failure bucket.

    Uses heuristics based on exit reason, MAE/MFE, and context.
    """
    if trade.r_multiple >= 0:
        return None  # Not a failure

    exit_reason = trade.exit_reason

    # BAD_STOP: Stopped out but would have been profitable
    if exit_reason == "STOP_LOSS":
        # Check if MFE was significant (would have hit TP1)
        if trade.mfe and trade.mfe >= trade.r_value * 0.5:
            return "BAD_STOP"  # Stop was too tight

    # BAD_ENTRY_TIMING: Early stop-out, MAE hit almost immediately
    if exit_reason == "STOP_LOSS" and trade.bars_held <= 2:
        return "BAD_ENTRY_TIMING"

    # BAD_TP_TRAILING: Trailing stop hit after significant MFE
    if exit_reason in ("TRAILING_STOP", "TIME_STOP"):
        if trade.mfe and trade.mfe >= trade.r_value:
            return "BAD_TP_TRAILING"  # Should have taken profit earlier

    # BAD_REGIME: Large market move against position
    if abs(trade.r_multiple) > 2.0:
        # Check if it was a regime shift (e.g., BTC shock)
        if trace.exit_conditions.get("btc_shock"):
            return "BAD_REGIME"

    # LIQUIDITY_ISSUE: Significant slippage
    if trade.fees_paid and trade.fees_paid > 0.005 * trade.entry_price:  # >0.5%
        return "LIQUIDITY_ISSUE"

    # SIGNAL_CONFLICT: Multiple signals, chose wrong one
    if trace.state_snapshot.get("conflicting_signals"):
        return "SIGNAL_CONFLICT"

    return "OTHER"


def classify_winner(trade: CCTrade, trace: TradeTrace) -> str:
    """
    Classify a winning trade into a winner bucket.

    v6 addition (canon compliance): Diagnostics on wins feed hypotheses
    but don't directly trigger changes unless converging with loser buckets.
    """
    if trade.r_multiple < 0:
        return None  # Not a winner

    # EARLY_EXIT: Left significant profit on the table
    # MFE was much larger than realized R (e.g., MFE > 2 * realized)
    if trade.mfe and trade.r_multiple > 0:
        potential_r = trade.mfe / trade.r_value if trade.r_value else 0
        if potential_r > 2 * trade.r_multiple:
            return "EARLY_EXIT"

    # REGIME_TAILWIND: Strong market move in our favor
    if trade.r_multiple > 2.0 and trade.bars_held <= 3:
        return "REGIME_TAILWIND"

    # ENTRY_PRECISION: Very low MAE relative to R
    if trade.mae and abs(trade.mae) < 0.2 * trade.r_value:
        return "ENTRY_PRECISION"

    # STOP_NEVER_THREATENED: MAE never came close to stop
    if trade.mae and abs(trade.mae) < 0.3 * trade.r_value:
        return "STOP_NEVER_THREATENED"

    # HIGH_SLIPPAGE_WIN: Won despite slippage
    if trade.fees_paid and trade.fees_paid > 0.003 * trade.entry_price:  # >0.3%
        return "HIGH_SLIPPAGE_WIN"

    # CONFLICTING_SIGNAL_WIN: Won despite conflicts
    if trace.state_snapshot.get("conflicting_signals"):
        return "CONFLICTING_SIGNAL_WIN"

    return "CLEAN_WIN"


# =============================================================================
# Module-to-bucket mappings for convergence detection
# =============================================================================

LOSER_BUCKET_TO_MODULE = {
    "BAD_REGIME": "REGIME",
    "BAD_ENTRY_TIMING": "ENTRY",
    "BAD_STOP": "STOP",
    "BAD_TP_TRAILING": "EXIT",
    "LIQUIDITY_ISSUE": "FILTER",
    "SIGNAL_CONFLICT": "SCORE",
}

WINNER_BUCKET_TO_MODULE = {
    "EARLY_EXIT": "EXIT",
    "REGIME_TAILWIND": "REGIME",
    "ENTRY_PRECISION": "ENTRY",
    "STOP_NEVER_THREATENED": "STOP",
    "HIGH_SLIPPAGE_WIN": "FILTER",
    "CONFLICTING_SIGNAL_WIN": "SCORE",
}


def get_next_tweak_recommendation(run_id: str) -> dict:
    """
    Analyze postmortems and recommend next module to tweak.

    v6 (canon compliance): Only recommend when winner and loser buckets
    CONVERGE on the same module. This implements the non-confounding rule:
    "changes only when winner-side and loser-side point to same module."
    """
    loser_buckets = get_loser_bucket_counts(run_id)
    winner_buckets = get_winner_bucket_counts(run_id)

    total_losses = sum(loser_buckets.values()) if loser_buckets else 0
    total_wins = sum(winner_buckets.values()) if winner_buckets else 0

    if total_losses == 0:
        return {
            "module_kind": None,
            "reason": "NO_FAILURES_TO_ANALYZE",
            "loser_buckets": loser_buckets,
            "winner_buckets": winner_buckets,
        }

    # Module priority order (canon)
    priority_order = ["FILTER", "REGIME", "ENTRY", "STOP", "EXIT", "SCORE"]

    # Compute module scores from both sides
    loser_module_scores = {}
    for bucket, count in loser_buckets.items():
        module = LOSER_BUCKET_TO_MODULE.get(bucket)
        if module:
            loser_module_scores[module] = loser_module_scores.get(module, 0) + count

    winner_module_scores = {}
    for bucket, count in winner_buckets.items():
        module = WINNER_BUCKET_TO_MODULE.get(bucket)
        if module:
            winner_module_scores[module] = winner_module_scores.get(module, 0) + count

    # Find convergence: modules that appear in BOTH loser and winner analysis
    converging_modules = []
    for module in priority_order:
        loser_count = loser_module_scores.get(module, 0)
        winner_count = winner_module_scores.get(module, 0)

        if loser_count > 0 and winner_count > 0:
            # Both sides point to this module
            converging_modules.append({
                "module_kind": module,
                "loser_count": loser_count,
                "winner_count": winner_count,
                "combined_signal_strength": loser_count + winner_count,
                "loser_pct": round(loser_count / total_losses * 100, 1),
                "winner_pct": round(winner_count / total_wins * 100, 1) if total_wins > 0 else 0,
            })

    if not converging_modules:
        # No convergence - report hypothesis only (no action recommended)
        top_loser_module = max(loser_module_scores.items(), key=lambda x: x[1])[0] if loser_module_scores else None

        return {
            "module_kind": None,
            "reason": "NO_CONVERGENCE",
            "hypothesis_from_losses": top_loser_module,
            "loser_module_scores": loser_module_scores,
            "winner_module_scores": winner_module_scores,
            "note": "Winner and loser buckets don't converge on same module. "
                    "Wait for more data or investigate manually.",
        }

    # Return highest-priority converging module
    # (converging_modules is already in priority order)
    recommendation = converging_modules[0]
    recommendation["converging_alternatives"] = converging_modules[1:] if len(converging_modules) > 1 else []

    return recommendation
```

---

## 9) API Endpoints

### 9.1 Management Endpoints

```python
# Backend/api/app/cc_api.py

router = APIRouter(prefix="/cc", tags=["Champion/Challenger"])


@router.post("/variant")
def create_variant(
    setup_id: str = Body(...),
    version: str = Body(...),
    modules: list[dict] = Body(...),
    status: str = Body("BASELINE"),  # BASELINE or CHAMPION for initial
    notes: str = Body(None),
) -> dict:
    """Create a new variant (typically a BASELINE or initial CHAMPION)."""
    ...


@router.post("/challenger/from/{champion_variant_id}")
def create_challenger(
    champion_variant_id: str,
    diff_type: str = Body(...),           # "MODULE" | "PARAM_BUNDLE" | "SCORE_COMPONENT"
    diff_module_kind: str = Body(...),    # "FILTER" | "REGIME" | etc.
    diff_key: str = Body(...),            # Descriptive: "EXIT.trailing"
    new_module_id: str = Body(None),      # For MODULE type
    new_params: dict = Body(None),        # For PARAM_BUNDLE or SCORE_COMPONENT type
) -> dict:
    """
    Create Challenger from Champion.

    Enforces:
    - One-module-different rule
    - Weekly cadence (7 days since last change)
    - No existing active challenger for this setup
    """
    ...


@router.post("/run")
def start_experiment_run(
    baseline_variant_id: str = Body(None),    # Optional, uses setup's designated baseline
    champion_variant_id: str = Body(...),
    challenger_variant_id: str = Body(...),
    mode: str = Body("SHADOW"),               # SHADOW | LIVE_SMALL | SPLIT
    timeframes: list[str] = Body(["1h"]),
    asset_universe: list[str] = Body(None),   # None = use current universe
    notes: str = Body(None),
) -> dict:
    """
    Start a new experiment run.

    Freezes module maps and execution spec at start.

    BASELINE ENFORCEMENT (Canon-critical):
    - If baseline_variant_id not provided, lookup from cc:baseline:{setup_id}
    - If no baseline exists for this setup, REFUSE to start run
    - Baseline must ALWAYS be included in 3-way comparison
    """
    # Resolve baseline
    setup_id = get_variant_setup(champion_variant_id)
    if baseline_variant_id is None:
        baseline_variant_id = r.get(cc_baseline_key(setup_id))
        if baseline_variant_id is None:
            raise HTTPException(
                status_code=400,
                detail=f"No baseline defined for setup {setup_id}. "
                       f"Create a BASELINE variant first via POST /cc/variant"
            )

    # Freeze all module maps at run start
    baseline_snapshot = load_and_freeze_module_map(baseline_variant_id)
    champion_snapshot = load_and_freeze_module_map(champion_variant_id)
    challenger_snapshot = load_and_freeze_module_map(challenger_variant_id)
    execution_snapshot = get_default_execution_spec().to_dict()

    # Create run with all snapshots
    ...


@router.post("/run/{run_id}/close")
def close_experiment_run(run_id: str) -> dict:
    """End an experiment run and compute final metrics."""
    ...


@router.post("/run/{run_id}/promote")
def attempt_promotion(run_id: str) -> dict:
    """
    Attempt to promote Challenger to Champion.

    Returns gate results. Executes promotion if all gates pass.
    """
    ...
```

### 9.2 Read Endpoints

```python
@router.get("/champions")
def list_champions(
    setup_id: str = Query(None),
) -> dict:
    """List current Champions (and Baselines) per setup."""
    ...


@router.get("/variants")
def list_variants(
    setup_id: str = Query(None),
    status: str = Query(None),
    limit: int = Query(50),
) -> dict:
    """List variants with optional filters."""
    ...


@router.get("/variant/{variant_id}")
def get_variant_detail(variant_id: str) -> dict:
    """Get full variant detail including module map."""
    ...


@router.get("/runs")
def list_runs(
    status: str = Query(None),
    setup_id: str = Query(None),
    limit: int = Query(20),
) -> dict:
    """List experiment runs."""
    ...


@router.get("/run/{run_id}/metrics")
def get_run_metrics(run_id: str) -> dict:
    """Get aggregated metrics for all variants (baseline + champ + chall) in a run."""
    ...


@router.get("/run/{run_id}/assets")
def get_run_per_asset_metrics(run_id: str) -> dict:
    """Get per-asset metrics for generalization gate inspection."""
    ...


@router.get("/run/{run_id}/trades")
def get_run_trades(
    run_id: str,
    variant_id: str = Query(None),
    limit: int = Query(100),
) -> dict:
    """Get trades from an experiment run."""
    ...


@router.get("/diagnostics")
def get_trade_diagnostics(
    run_id: str = Query(None),
    variant_id: str = Query(None),
) -> dict:
    """
    Get trade diagnostics with both winner and loser buckets (v6).

    Returns:
    - loser_buckets: Counts per failure bucket
    - winner_buckets: Counts per winner bucket
    - convergence: Modules where both sides point to same area
    - recommendation: Next-tweak recommendation (only if convergence exists)
    """
    ...


@router.get("/change-ledger")
def get_change_ledger(
    setup_id: str = Query(None),
    limit: int = Query(20),
) -> dict:
    """Get change history for weekly cadence visibility."""
    ...
```

---

## 10) File Structure

```
Backend/
├── worker/
│   └── app/
│       ├── cc/                           # NEW: Champion/Challenger module
│       │   ├── __init__.py
│       │   ├── models.py                 # Variant, ExperimentRun, ShadowTrade dataclasses
│       │   ├── registry.py               # Variant creation, module map, one-diff enforcement
│       │   ├── evaluator.py              # ParallelEvaluator, VariantStateBackend
│       │   ├── simulator.py              # ShadowTradeSimulator (reuses ExecutionSpec)
│       │   ├── promotion.py              # Promotion gates logic
│       │   ├── diagnostics.py            # Trade diagnostics (v6: winners + losers)
│       │   └── repository.py             # DB access layer
│       ├── signal_worker.py              # MODIFY: Add CC overlay evaluation
│       └── setups/
│           └── ...                       # Existing setups (unchanged)
├── api/
│   └── app/
│       ├── main.py                       # MODIFY: Mount CC router
│       └── cc_api.py                     # NEW: CC API endpoints
├── docs/
│   └── db/
│       └── schema/
│           ├── mcas_schema.sql           # MODIFY: Add CC tables (or include cc_schema.sql)
│           └── cc_schema.sql             # NEW: CC-specific schema file
```

---

## 11) Implementation Order

1. **Schema** (`cc_schema.sql`) - New tables including change_ledger, frozen snapshots
2. **Models** (`cc/models.py`) - Dataclasses for Variant, Module, ExperimentRun, ShadowTrade
3. **Repository** (`cc/repository.py`) - DB access layer + change ledger queries
4. **Registry** (`cc/registry.py`) - Variant creation, one-diff enforcement, weekly cadence check
5. **State Backend** (`cc/evaluator.py`) - VariantStateBackend with namespaced keys
6. **Simulator** (`cc/simulator.py`) - ShadowTradeSimulator reusing ExecutionSpec
7. **Evaluator** (`cc/evaluator.py`) - ParallelEvaluator with 3-way comparison
8. **Promotion** (`cc/promotion.py`) - Gate checks with empty-set guards, promotion execution
9. **Diagnostics** (`cc/diagnostics.py`) - Trade diagnostics (v6: winner + loser classification, convergence)
10. **API** (`cc_api.py`) - Endpoints including change-ledger
11. **Integration** - Modify `signal_worker.py` for CC overlay pattern
12. **Main** - Mount router in `main.py`

---

## 12) Configuration

```python
@dataclass
class CCConfig:
    """Champion/Challenger configuration."""

    # Promotion gates
    min_expectancy_improvement_pct: float = 0.0
    max_drawdown_increase_pct: float = 5.0
    min_assets_improved: int = 3
    min_trades_per_asset: int = 5
    use_median_comparison: bool = True

    # Complexity control (ENFORCED)
    change_cadence_days: int = 7           # Min days between changes per setup
    require_baseline_running: bool = True  # Baseline must always be in experiment

    # Evaluation
    default_mode: str = "SHADOW"
    min_run_duration_hours: int = 168      # 1 week minimum
    state_ttl_days: int = 14               # 2x min_run_duration to handle delays/downtime

    # Postmortem
    auto_classify_failures: bool = True
    failure_bucket_threshold: int = 5  # Min failures to suggest tweak

    # Exit logic (MVP scope - documented, not configurable)
    # Implements: STOP_LOSS → TIME_STOP → TP1
    # Future: FAIL_FAST, TRAILING_STOP, TP2
```

---

## 13) Acceptance Criteria

### Core Functionality
1. [ ] Can define a Champion variant with full module map
2. [ ] Can define a Baseline variant (dead-simple, runs forever)
3. [ ] Can create Challenger from Champion with exactly one module different
4. [ ] One-module-different rule enforced (rejects >1 diff)
5. [ ] SCORE_COMPONENT diff_type supported in creation logic
6. [ ] Weekly cadence enforced (blocks challenger creation if <7 days since last change)
7. [ ] Active challenger check (blocks if setup already has active challenger)

### Parallel Evaluation
8. [ ] Can start experiment run with 3-way comparison (baseline + champ + chall)
9. [ ] Baseline enforced: run refuses to start if no baseline exists for setup
10. [ ] Variant state namespaced: no collision between variants on same symbol/tf
11. [ ] Shadow position keys include timeframe: no collision on multi-TF runs
12. [ ] **One ShadowTradeSimulator per variant**: no shared pending signals/exits
13. [ ] Variants cached at run start (no per-bar DB load)
14. [ ] Module maps frozen at run start (mid-run edits have no effect)
15. [ ] CC runs as overlay: other setups continue normal evaluation
16. [ ] Shadow trade simulation uses SAME functions as paper trading (not reimplemented)
17. [ ] Entry at next bar OPEN (not signal bar close): pending signal pattern
18. [ ] State TTL = 14 days with refresh on every write

### Exit Logic (Canon Compliance)
19. [ ] Time-stop uses `bars_held >= N_dead` (not `>`)
20. [ ] Time-stop exit at next bar OPEN via `_process_pending_exits()` with slippage/fees
21. [ ] `half_r_touched` computed for BOTH long AND short positions
22. [ ] MVP exit priority: STOP_LOSS → TIME_STOP → TP1 (FAIL_FAST/TRAILING documented as future)
23. [ ] All exits apply slippage and fees via shared execution model functions

### Metrics & Promotion
24. [ ] Metrics aggregated per-variant (expectancy, drawdown, win_rate)
25. [ ] Per-asset metrics computed for generalization gate
26. [ ] Promotion attempt:
    - [ ] Blocks when expectancy not improved
    - [ ] Blocks when drawdown worsens beyond tolerance
    - [ ] Blocks when improvement doesn't generalize
    - [ ] Blocks explicitly when insufficient sample size (not crash)
    - [ ] Promotes when all gates pass
27. [ ] Old Champion marked RETIRED on promotion
28. [ ] New Champion reflected in Redis quick lookup
29. [ ] Promotion recorded in change ledger

### Postmortem
30. [ ] Failure bucketizer classifies losing trades
31. [ ] Next-tweak recommendation follows canon priority order
32. [ ] Handles empty failure set gracefully

### API & DB
33. [ ] pgcrypto extension ensured in schema
34. [ ] `cc_experiment_trades.id` is TEXT (matches Python str UUID)
35. [ ] `cc_postmortems.trade_id` is TEXT (FK constraint compatible with trades.id)
36. [ ] API endpoints functional (see section 9)
37. [ ] Change ledger queryable for cadence visibility

### Safety
38. [ ] No disruption to normal S1/S2/U1 evaluation when CC not active
39. [ ] **Baseline ALWAYS evaluates** (Option B): even when no CC run is active, via `_evaluate_with_baseline()`

### State & Cache Management (v5/v6)
40. [ ] State backend enforces namespace: `_assert_namespace()` parses key by `:` and checks exact field positions
41. [ ] Gate 3 requires BOTH champion AND challenger to have `min_trades_per_asset` before counting asset
42. [ ] Evaluator cache uses `CCEvaluatorCacheEntry` dataclass (not raw tuples)
43. [ ] `_evict_cc_evaluator(run_id)` called on run completion/closure
44. [ ] `_evict_stale_cc_evaluators()` available for periodic cleanup of stale entries
45. [ ] Expectancy improvement % handles `champ_expectancy == 0` without `inf` confusion
46. [ ] All `created_at` timestamps are server-generated (Postgres `NOW()`), stored in UTC
47. [ ] Weekly cadence uses `total_seconds()` comparison (not `.days` truncation)

### Trade Diagnostics (v6 - Canon Compliance)
48. [ ] `classify_winner()` classifies winning trades into winner buckets
49. [ ] Winner buckets defined: EARLY_EXIT, REGIME_TAILWIND, ENTRY_PRECISION, STOP_NEVER_THREATENED, HIGH_SLIPPAGE_WIN, CONFLICTING_SIGNAL_WIN, CLEAN_WIN
50. [ ] `get_next_tweak_recommendation()` only recommends when winner and loser buckets CONVERGE on same module
51. [ ] When no convergence, returns `reason: "NO_CONVERGENCE"` with hypothesis only (no action)

### State Persistence & Safety (v7)
52. [ ] `cc_shadow_positions` table exists for Postgres backup of Redis state
53. [ ] `cc_pending_signals` table exists for Postgres backup of pending signals
54. [ ] On Redis miss, system attempts Postgres recovery before treating as "no state"
55. [ ] `ExecutionContext` enum defined with LIVE/PAPER/SHADOW values
56. [ ] `assert_shadow_safe()` guard prevents side-effecting operations in SHADOW context
57. [ ] Test exists proving `apply_slippage()` works in SHADOW context
58. [ ] Test exists proving `submit_order()` raises in SHADOW context
59. [ ] Pending signals track `bars_pending` counter
60. [ ] `MAX_PENDING_BARS` constant defined (default 3)
61. [ ] Stale pending signals (> MAX_PENDING_BARS) are cancelled with warning
62. [ ] `age_pending_signals()` method increments bars_pending for un-consumed signals
63. [ ] Stale pending exits force-resolve with warning
64. [ ] Schema applied via migration files, not ad-hoc runtime DDL
65. [ ] Migration runner acquires advisory lock before running

### Required Scheduling & Monitoring (v8)
66. [ ] `heartbeat_refresh_all_positions()` scheduled to run every `HEARTBEAT_INTERVAL_HOURS` (24h)
67. [ ] Alert configured if heartbeat hasn't run in 2x `HEARTBEAT_INTERVAL_HOURS`
68. [ ] `_evict_stale_cc_evaluators()` scheduled to run every `CACHE_CLEANUP_INTERVAL_SECONDS` (1h)
69. [ ] Alert configured if cache size exceeds `CACHE_SIZE_ALERT_THRESHOLD` (100)

### Concurrency & Locking (v8)
70. [ ] `execute_promotion()` uses `FOR UPDATE NOWAIT` on run row
71. [ ] `execute_promotion()` checks run status is RUNNING before proceeding (race guard)
72. [ ] `execute_promotion()` commits Redis update AFTER transaction commits
73. [ ] `start_experiment_run()` enforces one active run per setup via transactional check

### ID Validation (v8)
74. [ ] `FORBIDDEN_ID_CHARS` constant defined (includes ":")
75. [ ] `validate_identifier()` rejects identifiers containing forbidden characters
76. [ ] Variant creation validates setup_id and variant_id
77. [ ] Run creation validates all identifiers
78. [ ] Signal recording validates symbol and timeframe

### Safe Metrics (v8)
79. [ ] `safe_pct()` returns default (not inf/NaN) when denominator is zero
80. [ ] `safe_ratio()` returns default (not inf/NaN) when denominator is zero
81. [ ] `safe_improvement_pct()` returns `(None, description)` for edge cases instead of inf
82. [ ] Gate 1 uses `safe_improvement_pct()` and stores both `improvement_pct` and `improvement_desc`
83. [ ] No inf or NaN values written to `cc_run_metrics` table

### Migration-Only DDL (v8)
84. [ ] CC schema SQL lives in `Backend/docs/db/migrations/` directory only
85. [ ] No `CREATE TABLE` or `CREATE INDEX` statements in Python application code
86. [ ] No schema execution in worker `__init__` or API startup
87. [ ] Migration applied via `psql -f` or Alembic at deploy time
88. [ ] Code review checklist includes "no DDL in application code" check

---

## 14) Resolved Issues from Review

### v1 → v2 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 1 | Variant state collision risk | State keys namespaced: `cc:state:{run_id}:{variant_id}:{setup_id}:{symbol}:{tf}` |
| 2 | signal_worker integration can disable normal eval | CC implemented as overlay per setup; other setups run normal |
| 3 | Loading variants per bar is performance hazard | Variants cached at run start; module maps frozen in `cc_experiment_runs` |
| 4 | Trade outcome simulation not specified | `ShadowTradeSimulator` reuses `ExecutionSpec` from paper trading |
| 5 | Baseline "always running forever" not implemented | 3-way comparison: baseline + champ + chall; baseline_variant_id in runs |
| 6 | "One module per week" not enforced | `cc_change_ledger` table + cadence check in `create_challenger_from_champion()` |
| 7 | SCORE_COMPONENT not in creation logic | Added as first-class diff_type path |
| 8 | Diff-key semantics inconsistent | Standardized: `diff_module_kind` (enum) + `diff_key` (descriptive string) |
| 9 | gen_random_uuid() requires extension | `CREATE EXTENSION IF NOT EXISTS pgcrypto;` at schema top |
| 10 | Median calculation can crash on empty sets | Explicit guard: gate fails with `INSUFFICIENT_SAMPLE_SIZE` reason |

### v2 → v3 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 11 | Shadow position key missing timeframe | Key now includes tf: `cc:shadow_pos:{run_id}:{variant_id}:{symbol}:{tf}` |
| 12 | Entry pricing contradicts doc (used bar close, not next open) | Pending signal pattern: `record_signal()` stores, `process_pending_entries()` opens at next bar's OPEN |
| 13 | Time-stop exit not canon-consistent | Fixed: `bars_held >= time_stop_bars` (not >), exit at next bar open via `exit_at_next_open` flag, SHORT side `half_r_touched` added |
| 14 | State TTL too short for "runs forever" baseline | TTL increased to 14 days (2× min run duration); explicit note to refresh TTL on every write |
| 15 | Baseline required but schema allows NULL | Enforced at `start_experiment_run()`: lookup from Redis, refuse if no baseline exists |
| 16 | Exit priority declared but not fully implemented | Clarified MVP scope: implements STOP_LOSS → TIME_STOP → TP1; FAIL_FAST/TRAILING noted as future |
| 17 | "Same execution model" not guaranteed | Added explicit requirement: MUST call same functions from `execution.py` / `paper_trading.py` |

### v3 → v4 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 18 | **CRITICAL**: Shared ShadowTradeSimulator across variants | One simulator per variant: `self.variants[role] = (variant, ShadowTradeSimulator(...))` - prevents signal/exit collision |
| 19 | TIME_STOP "exit at next open" incomplete | Added `_process_pending_exits()` called at START of bar, uses `bar.open` + slippage/fees via shared execution model |
| 20 | UUID type mismatch (Postgres UUID vs Python str) | Changed `cc_experiment_trades.id` and `execution_id` to TEXT to match Python `str(uuid.uuid4())` |
| 21 | "Baseline runs forever" ambiguous | Explicit interpretation added: MVP = baseline in every CC run; literal "always on" is optional future enhancement |
| 22 | FK constraint mismatch: `cc_postmortems.trade_id` UUID vs TEXT | Changed `cc_postmortems.trade_id` to TEXT to match `cc_experiment_trades.id` |
| 23 | Immediate exit `exit_ts` used `bar.open_time` | Changed to `bar.close_time` for touch model exits (STOP/TP) to match paper_trading.py semantics |

### v4 → v5 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 24 | **Gate 3 can count "unfair wins"**: Challenger "generalizes" by beating champion metrics that are statistically meaningless (champion had <5 trades on that symbol) | Require BOTH champion AND challenger to have `min_trades_per_asset` before: (1) counting toward `assets_with_enough_trades`, (2) allowing contribution to `assets_improved`. Prevents false promotions. |
| 25 | State isolation not enforced as hard invariant | Added `VariantStateBackend._assert_namespace()` guard that refuses reads/writes unless key includes all required fields (`run_id`, `variant_id`, `symbol`, `tf`). Runtime check prevents "heisenbug" state collision. |
| 26 | Evaluator cache can grow without bound in long-lived worker | Added explicit cache eviction: (1) `_evict_cc_evaluator(run_id)` called on run completion/closure, (2) `_evict_stale_cc_evaluators(max_age_seconds)` TTL-based fallback for un-closed runs. Cache entries now track `last_used` timestamp. |
| 27 | Expectancy improvement % goes to `inf` when champion=0 | Added edge case handling: if `champ_expectancy == 0`, report `+inf` only if challenger > 0, `0.0` if both zero, `-inf` if challenger negative. Prevents confusing dashboard/audit values. |
| 28 | Weekly cadence wall-clock drift risk | Clarified: `created_at` is server-generated (`NOW()`) in UTC. Added comment in cadence check code noting DB-side comparison option if API clock skew is a concern. |

### v5 → v6 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 29 | **Cache structure mismatch**: Eviction code expected `(evaluator, last_used)` tuples but creation stored raw `ParallelEvaluator` | Defined canonical `CCEvaluatorCacheEntry` dataclass with `evaluator` and `last_used` fields. All cache operations use this structure. |
| 30 | **Namespace enforcement can false-pass/fail**: `_assert_namespace()` used substring check (`if part not in key`) | Parse key by splitting on `:` and compare exact field positions. Added constants for expected field count and positions. |
| 31 | **Weekly cadence off by ~24h**: `.days` truncates (6d23h reads as 6) | Use `total_seconds()` and compare to `7*86400`. Display remaining time in hours when < 24h. |
| 32 | **"Baseline runs forever" was interpretive MVP**: Baseline only ran during CC experiments | Implemented Option B: Baseline ALWAYS evaluates alongside champion, even when no CC experiment is active. Added `_evaluate_with_baseline()` for 2-way eval. |
| 33 | **Diagnostics only on losers (canon drift)**: Plan was "losers-first learning" but canon requires diagnostics on wins AND losses | Added `classify_winner()` and winner buckets (EARLY_EXIT, REGIME_TAILWIND, etc.). Changed `get_next_tweak_recommendation()` to only recommend when winner and loser buckets CONVERGE on same module (non-confounding rule). |

### v6 → v7 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 34 | **Redis TTL (14 days) can silently delete state mid-run**: Long-lived holding state that goes quiet can vanish, causing phantom exits / missing trades | Added `cc_shadow_positions` and `cc_pending_signals` Postgres tables as backup. On Redis miss, attempt Postgres recovery. Added daily heartbeat refresh concept for open positions. |
| 35 | **"Reuse same functions" can cause side-effects**: Execution functions might mutate shared state or publish to live queues | Added `ExecutionContext` enum (LIVE/PAPER/SHADOW) and `assert_shadow_safe()` guard. All execution functions must check context before side-effecting operations. Required tests proving shadow runs cannot enqueue real orders. |
| 36 | **Next-bar OPEN entry/exit can deadlock on data gaps**: Pending signals/exits can remain stuck indefinitely if feed misses bars | Added `MAX_PENDING_BARS = 3` constant. Pending signals track `bars_pending` counter; cancelled with warning if exceeds max. Added `age_pending_signals()` method. Pending exits force-resolve with warning if stale. |
| 37 | **Variant IDs + DB typing: watch migrations**: Ad-hoc DDL at runtime causes deploy-time race/locking issues | Added migration guidance requiring proper migration files, advisory locks, `schema_migrations` table, and deploy-time (not runtime) execution. |

### v7 → v8 Fixes

| # | Issue | Resolution |
|---|-------|------------|
| 38 | **Redis TTL + "quiet" state loss needs discipline**: Heartbeat/recovery was proposed but treated as optional | Made heartbeat REQUIRED: added `HEARTBEAT_INTERVAL_HOURS = 24`, `heartbeat_refresh_all_positions()` function with explicit scheduling and monitoring requirements. Alert if task hasn't run in 2x interval. |
| 39 | **Promotion concurrency / double-promotion race**: Two workers attempting promotion could corrupt status | Wrapped `execute_promotion()` in single DB transaction with `FOR UPDATE NOWAIT` lock. Added race condition check (refuse if already PROMOTED). Added `start_experiment_run()` transactional check for one active run per setup. |
| 40 | **Namespace enforcement: ":" in IDs breaks parsing**: If identifiers contain colons, key parsing invariant fails | Added `FORBIDDEN_ID_CHARS`, `validate_identifier()`, and `validate_all_identifiers()` functions. All ID creation points must validate and reject colons. |
| 41 | **Evaluator cache periodic sweeper must actually run**: Relying on it but no scheduling guarantees | Added `CACHE_CLEANUP_INTERVAL_SECONDS`, explicit scheduling options (APScheduler, Celery Beat), monitoring requirements, and `CACHE_SIZE_ALERT_THRESHOLD` with warning log. |
| 42 | **Metrics edge cases: inf/NaN can propagate to UI/DB**: All derived % metrics need same discipline | Added `safe_pct()`, `safe_ratio()`, and `safe_improvement_pct()` helpers. Gate 1 now uses `safe_improvement_pct()` returning `(pct, description)` tuple with None for edge cases instead of inf. |
| 43 | **DDL still in plan could be copy-pasted to runtime code**: SQL blocks looked like they could be executed from app code | Added prominent warnings: "⚠️ MIGRATION-ONLY SCHEMA", explicit DO/DON'T lists, file path for migration, example of wrong vs right patterns, closing END OF MIGRATION comment. Schema section now unambiguously documents migration file content only. |

---

## 15) Open Questions (Remaining)

1. **Auto-close**: Should runs auto-close after N weeks, or always manual?
2. **UI**: Any specific UI requirements for viewing C/C experiments?
3. **Backtest integration**: Should C/C support backtest-mode comparison (faster iteration)?
