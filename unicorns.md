
# MCAS — Unicorns Setup Scope (Canon-Faithful, AI-Coder Instructions)

## 0) Purpose

Implement the **Unicorns pipeline** to identify **low-volume pennycoins** that are transitioning from **dead → discoverable → ignition** *before* they appear in normal “active assets” lists.

Unicorns are **not** an execution automation feature. This scope is **identification + ranking + signal generation** (human-in-loop remains).

## 1) Canon constraints (must hold)

1. **Modular setups**: Unicorns must be a plug-and-play setup module (like S1), not entangled with other setups.
2. **API-first**: backend produces all truth; UI only consumes.
3. **Compute-in-cache**: keep live state in Redis/cache; do **not** write rolling indicator history to Postgres. Persist only snapshots/events needed for audit (e.g., on signal creation, and on trade execution).
4. **Cost efficiency**: real-time pennycoin coverage is **budget constrained**; the system must enforce a hard cap on the size of the “Embryo Active” watchlist.

## 2) High-level pipeline (canonical)

We maintain three phases:

### Phase A — Embryo Universe (broad, cheap scan)

A large pool of candidates that are **tradeable but ignored**.

### Phase B — Embryo Active (paid attention budget)

A capped list of candidates that passed “pre-ignition” gates and now get **higher cadence monitoring**.

### Phase C — Ignition Watch → Unicorn Signal

Within Embryo Active, detect **ignition triggers** and emit a **Unicorn signal** (human decides whether to execute).

---

## 3) Data requirements and graceful degradation

### 3.1 Required (MVP Unicorns must work with these)

* **OHLCV** at minimum: 5m (preferred) + optionally 15m/1h for context.
* **Trade prints** (or equivalent) for: small trade count, buy/sell aggressor ratio proxies.
* **Order book** (best bid/ask + depth snapshots) for: spread bps, depth within ±X bps, slippage proxy.

If a venue/data source cannot supply one of these, implement a **fallback**:

* No depth/slippage: use spread + volume + volatility compression only, but apply stricter demotion rules and lower scoring ceiling.
* No trade prints: approximate participation via volume distribution + number of candle “micro-moves” (last resort).

### 3.2 Optional enrichments (canon-referenced; implement if data available)

* **On-chain holders**: unique holders delta, churn-adjusted growth.
* **News velocity**: positive rate-of-change in mentions with low absolute saturation.
* **Correlation isolation**: low correlation with BTC/sector over a window.
* **No prior hype cycle**: exclude assets that recently had a major hype spike (defined below).

All optional enrichments must be implemented as **feature flags**, not hard dependencies.

---

## 4) Core definitions

### 4.1 Asset state machine

Each candidate asset is always in exactly one state:

* `EMBRYO`
* `EMBRYO_ACTIVE`
* `IGNITION_WATCH` (can be represented as a substate/flag within EMBRYO_ACTIVE)
* `DELISTED/REJECTED` (hard filters failed)

### 4.2 Attention budget (hard cap)

* Config: `MAX_EMBRYO_ACTIVE = N`
* N is determined by data plan economics (monthly cost / supported assets @ required cadence).
* **No soft exceptions.** Promotion is zero-sum: to add a new asset to Embryo Active when full, **evict the lowest score**.

---

## 5) Hard reject filters (apply before any scoring)

If any condition is true → state becomes `REJECTED` (or remains `EMBRYO` but with “hard block”):

### 5.1 CEX / general (must-have tradability)

* Symbol fails exchange precision / min notional constraints.
* Spread is persistently extreme: `median(spread_bps_last_n) > SPREAD_BPS_MAX_HARD`
* Order book is too thin (if depth available): `depth_usd(±50bps)_median < DEPTH_USD_MIN_HARD`

### 5.2 DEX-only (if DEX supported later)

* Liquidity not locked / can be pulled (if data exists)
* Honeypot / sell blocked / extreme tax (if data exists)
* Contract is mutable/mintable without constraints (if data exists)

(MVP may be CEX-only; keep DEX rules as future toggle.)

---

## 6) Embryo → Embryo Active promotion (deterministic gates)

Promotion requires passing **Gate 1 → Gate 2 → Gate 3**, and **Gate 4 if available**.

All gates are evaluated over two adjacent windows:

* `last_n` and `prev_n` (same length)
* Default: `n = 48` on 5m candles (4h). Parameterized.

### Gate 1 — Tradability improving (pass if 2 of 3)

1. Spread compression
   `median(spread_bps_last_n) <= 0.7 * median(spread_bps_prev_n)`
2. Depth improvement near mid (if depth exists)
   `median(depth_usd(±50bps)_last_n) >= 1.5 * median(depth_usd(±50bps)_prev_n)`
3. Slippage improvement for fixed test notional (if slippage proxy exists)
   `median(slippage_bps_$X_last_n) <= 0.7 * median(slippage_bps_$X_prev_n)`

### Gate 2 — Participation broadening (pass if all true)

1. Small trade count rising
   `small_trade_count_last_n >= 1.5 * small_trade_count_prev_n`
2. Unique buyers rising (or best proxy available)
   `unique_buyers_last_n >= 1.3 * unique_buyers_prev_n`
3. Whale dominance not present (or proxy)
   `top_trade_share_last_n < 0.35`
   (If whale dominance cannot be estimated, replace with: trade-size distribution not extremely skewed.)

### Gate 3 — Compression (pass if all true)

1. Range tight
   `range_pct_last_n <= RANGE_PCT_THRESHOLD`
   (Prefer percentile-based: ≤ P20 of trailing 30d; if expensive, use static threshold.)
2. Volatility contracting
   `ATR_pct_last_n <= 0.7 * ATR_pct_prev_n` (or BB width contraction)
3. Volume stops decaying (but not igniting yet)
   `slope(volume_last_n) >= 0` and `rel_volume_last_n < IGNITION_REL_VOL_MIN`

### Gate 4 — Holder integrity (only if on-chain available)

* `Δunique_holders_last_n > 0` AND
* churn-adjusted growth positive: `(new_holders - exited_holders) > 0`

**Promotion result**:

* State becomes `EMBRYO_ACTIVE` and `ignition_watch=true`
* Asset receives an `EmbryoActiveScore` and a timestamp `promoted_at`.

---

## 7) Ignition Watch (when to emit a Unicorn signal)

An asset in `EMBRYO_ACTIVE` emits a **UnicornIgnition signal** when the following are true:

### Trigger set (must be met)

1. Relative volume ignition
   `rel_volume_now >= REL_VOL_IGNITION` (default: 5–10× baseline)
   Baseline = median(volume) over last 7d for same timeframe.
2. Breakout + acceptance

* Breakout: price breaches range high (defined from compression box)
* Acceptance: holds above range high for `m` bars (default m=2–3 on 5m)

### Confirmation set (must meet at least 2 of 3)

A) Liquidity + volume rising together (avoid pump traps)

* If volume spikes but spread widens or slippage worsens → **reject signal** (see demotions)
  B) Participation confirmation
* unique buyers and small trade count continue rising vs prior window
  C) TrendRate acceleration from near-zero (canon)
* Compute TrendRate and its acceleration:

  * `TrendRate` should be a normalized trend strength/velocity metric available in MCAS indicators
  * Condition: `TrendRate` rising and `TrendRate_accel > 0` from a low base

### Optional canon enrichers (apply if available; increase confidence)

* **News velocity positive but low saturation**: mentions slope > 0 AND absolute mentions below saturation cap
* **Correlation isolation**: corr(asset,BTC) over window < threshold
* **No prior hype cycle**: exclude if asset had a previous spike with:

  * `max(rel_volume_30d) > HYPE_REL_VOL_CAP` OR
  * `max(range_pct_30d) > HYPE_RANGE_CAP`

**Signal output must include**:

* `signal_type = "UNICORN_IGNITION"`
* `symbol`, `timeframe`, `signal_ts`, `bar_start_ts`
* `age_seconds` (now - signal_ts)
* `action_by_ts` = signal_ts + (N_dead * bar_seconds)
  (deadline for human execution; aligns with canon “action-by” field)
* All gate/trigger booleans + key numeric metrics used (so it’s explainable)

---

## 8) Demotion rules (avoid garbage / pump traps)

Even if an asset is `EMBRYO_ACTIVE`, demote back to `EMBRYO` if any occurs:

1. **Pump liquidity trap**
   Volume spikes but tradability worsens:

* `spread_bps` rises sharply OR
* slippage proxy worsens OR
* depth collapses

2. **Non-broad participation**
   Participation spike dominated by a small number of large trades / extreme skew.

3. **Timeout decay (stagnation)**
   If no ignition within `T_active_max` since `promoted_at` (config), demote unless its score remains in top X% of Embryo Active.

Demotion must free a slot under the hard cap.

---

## 9) Scoring (ranking is mandatory, not optional)

### 9.1 Scores to compute

Compute these per asset (0–100):

* `TradabilityScore`
* `ParticipationScore`
* `CompressionScore`
* `IgnitionScore`
* `RugRiskScore` (or `TrapRiskScore` for CEX MVP)

Then compute:

* `EmbryoActiveScore = w1*T + w2*P + w3*C - w4*Risk`
* `UnicornConfidence = function(IgnitionScore, confirmations, optional enrichers)`

Weights are config-driven, with sensible defaults.

### 9.2 Ranking and cap enforcement

On each evaluation cycle:

* rank all eligible candidates by `EmbryoActiveScore`
* enforce `MAX_EMBRYO_ACTIVE` by:

  * promoting top candidates that pass gates
  * evicting lowest ranked if full

---

## 10) Cache/Storage contracts (must be deterministic)

### 10.1 Redis keys (conceptual; integrate with existing patterns)

* `unicorn:embryo_universe` (set/list of symbols)
* `unicorn:embryo_active` (sorted set by `EmbryoActiveScore`)
* `unicorn:state:{symbol}` (hash: state, promoted_at, last_eval_ts, flags)
* `unicorn:metrics:{symbol}` (hash: latest metrics + gate booleans)
* `signals:zset` existing signal index should include Unicorn signals with ranking key.

### 10.2 Postgres persistence (minimal)

* Persist only:

  * emitted Unicorn signals (for audit/history)
  * trade snapshots if executed
* Do **not** persist rolling metrics history.

---

## 11) API requirements (backend truth)

Add endpoints (names can follow existing API conventions):

* `GET /unicorns/embryo-active` → ranked list with metrics + gating status
* `GET /signals?setup=UNICORN` (or equivalent filter) → ignition signals
* `GET /unicorns/{symbol}` → explainability payload (why promoted, current gates/metrics)

All responses must include `age_seconds` and `action_by_ts` for signals.

---

## 12) UI requirements (human-in-loop usability)

Add a **Unicorns** view with two panes:

1. **Embryo Active Watchlist (ranked)**
   Show: score, state, tradability/participation/compression indicators, last update time.
2. **Ignition Signals**
   Show: age, action-by, confidence, key reasons (breakout accepted, rel vol, liquidity+volume, TrendRate accel).

No auto-execution. UI provides clarity + prioritization.

---

## 13) Acceptance criteria (done means done)

1. Assets are promoted to `EMBRYO_ACTIVE` only if Gate 1→2→3 (and 4 if available) are satisfied.
2. Hard cap `MAX_EMBRYO_ACTIVE` is strictly enforced.
3. Unicorn ignition signals emit only when ignition triggers + confirmations pass.
4. Every Unicorn signal includes `age_seconds` and `action_by_ts`.
5. Demotion rules operate and demonstrably remove pump traps.
6. System works without on-chain/news/correlation data (degrades gracefully), but uses them when enabled.
7. No rolling history stored in Postgres; cache-first behavior preserved.

---

## 14) Minimal test plan (must be included)

* Unit tests for each gate and trigger logic using synthetic time series and book/trade metrics.
* Regression test: ensure promotion/demotion is deterministic given fixed inputs.
* Contract test: API payload contains required fields and types, including `age_seconds`/`action_by_ts`.

---

### Notes for the implementer

* Keep Unicorns logic as its own setup module (e.g., `U1_UnicornIgnition`) with clearly named config params.
* Feature flags for optional enrichers must not alter core behavior when disabled.
* Prefer explainability over opacity: every promotion and signal should be “why” readable from the payload.

---

If you want, I can also produce a **companion “Implementation Task Breakdown”** (file-by-file deltas aligned to your current MCAS repo structure) so an AI coder can apply this in a single pass without guessing where to plug each piece.
