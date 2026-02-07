# MCAS Glossary (Canonical Definitions)

> **Canon §1.3**: Core definitions MUST live in exactly one glossary file.
> Do NOT redefine these terms in other docs/specs — link here instead.
> If semantics change, update this file + any dependent `docs/` specs in the same PR.

---

## Market

A specific trading venue + pair combination (e.g., Binance BTCUSDT).
OHLCV data is an attribute of a Market, not of an Asset.
Unique key: `(exchange, symbol)` — though MVP currently assumes Binance-only,
so `symbol` alone serves as market identity.

## Asset

A symbol abstraction (e.g., BTC, ETH) that can map to one or many Markets.
Assets are NOT the primary key for price data — Markets are.

## Timeframe

An explicit string identifying the candle interval: `"1m"`, `"5m"`, `"15m"`, `"1h"`, `"4h"`, `"1d"`.
Always stored and transmitted as a string, never as a numeric code or implicit default.

## Candle

A single OHLCV bar, identified by:
- **`bar_start_ts`** (UTC): the canonical candle identity — start of the interval.
- **`timeframe`**: which interval this candle belongs to.
- **`is_final`**: whether the candle is closed/complete (`true`) or still forming (`false`).

API responses may alias `bar_start_ts` as `candle_ts` for readability,
but if both are present they MUST be identical.

## Feature

A numeric indicator output computed from candle data (e.g., `RSI_14 = 62.3`).
Every feature maps to a canonical `indicator_id` and `version` in the Indicator Registry.
Features are computed by the Feature Factory (Tier 1-2) and are ephemeral by default
(stored in Redis ring buffers, not persisted to Postgres unless explicitly whitelisted).

## Signal

A setup-derived state indicating a potential trading opportunity (e.g., `S1_TRIGGERED`).
Signals carry:
- `setup_id`: which setup produced the signal
- `setup_score` + `score_components`: server-side ranking
- `since_ts`: when the signal entered its current state
- `action_by_ts`: latest valid time to act (time-stop deadline)
- `explain`: 3-8 human-readable "why" items

Signals are Tier 4 outputs — they depend on Tier 3 state computed by setup modules.

## Setup

A plug-in strategy module that evaluates Features to produce Signals.
Each setup implements the `SetupBase` contract:
- preconditions (regime/filters)
- trigger definition (no lookahead; `is_final` gated)
- entry definition (explicit fill model: next-bar open + slippage)
- stop/invalidation + time stop
- targets/trailing state machine
- concurrency rules + liquidity/sizing caps
- explainability payload (3-8 "why" items)

Active setups: S1 (BB Squeeze ON), S2 (Golden Cross Anticipation), U1 (Unicorn Ignition).

## Run

A bounded execution context: one of SCAN, BACKTEST, or PAPER.
Identified by a SHA256 fingerprint of its full specification
(universe + strategy + execution + data + indicator library version).
Duplicate fingerprints are rejected to prevent redundant computation.

## Snapshot

A frozen record at decision time, sufficient to reproduce the decision later.
Contains:
- canonical IDs + versions of all referenced indicators
- candle keys (`market_id`, `timeframe`, `bar_start_ts`)
- key feature values and score components
- reason codes / explainability payload

Stored as `trace_json` on trade records and as `data_snapshots` for backtest reproducibility.
