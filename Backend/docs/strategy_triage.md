# Strategy Triage Inventory
> Last updated: 2026-03-09

## Classification Key
- **ACTIVE**: In production signal generation (in SETUP_IDS)
- **PAPER**: Running in paper trading mode, not generating live signals
- **RESEARCH**: Has walk-forward results, not deployed
- **DEPRECATED**: Superseded or invalidated
- **ARCHIVED**: Code moved to Archive/setups/

## Production Signal Workers
Current `SETUP_IDS` env var default: `S9,U1,D1`

## STRATEGY_REGISTRY (api/app/main.py)
S1: legacy | S2: legacy | S6: legacy | S9: active | S10: active | U1: active | D1: active | D2: paper

---

## Strategies

### D1 -- Dormancy-to-Ignition
- **Status:** ACTIVE
- **Version:** v1.2
- **Setup type:** breakout
- **Direction:** LONG only
- **Last validation:** Config D sim matrix winner (daily PF 2.41, aggregate PF 3.59 -- noted in D1Config comments)
- **Daily PF:** 2.41 (Config D, 1.5% stop)
- **Win rate:** ~39.5% (Config D: 370/901 trades after fee/R guard)
- **DSR:** Not explicitly recorded in code
- **Timeframes:** 1h
- **Hold period:** 72 bars (3 days) time stop
- **Universe:** Microcap focus, 75K-3M USD 24h volume, excludes majors/stables/leveraged
- **Exit config:** 1.5% stop (Config D), 10% TP1, 20% TP2, 72-bar time stop
- **Known limitations:** Gap-through risk on illiquid symbols (documented in Corrections Log 2026-03-08). Social silence is a soft feature, not a gate. BTC beta fallback to 1.0 on noisy microcaps.
- **File:** `worker/app/setups/d1_dormancy_ignition.py`
- **Backfill:** `scripts/backfill_d1_opportunities.py`
- **Sim:** `scripts/sim_d1_matrix.py`

### U1 -- Unicorn Ignition
- **Status:** ACTIVE
- **Version:** v4.0 (WS2 Audit-Driven Refactor)
- **Setup type:** breakout
- **Direction:** LONG only
- **Last validation:** v4.0 gates data-validated via WS2 audit
- **Daily PF:** No validation data available in code
- **DSR:** No validation data available
- **Timeframes:** 5m, 15m (timeframe-specific volatility gates)
- **Hold period:** Configurable time stop
- **Universe:** Microcap pennycoin focus, 75K-3M USD 24h volume, excludes majors/stables/leveraged
- **Known limitations:** BB squeeze scoring zeroed (dead weight, r=-0.009). Reversion score weight (0.05) is an invented starting point, requires validation. Express Lane bypass path needs monitoring.
- **File:** `worker/app/setups/u1_unicorn_ignition.py`

### S9 -- Short Reversion
- **Status:** ACTIVE
- **Version:** v1.0
- **Setup type:** mean_reversion (cross-sectional)
- **Direction:** SHORT only
- **Last validation:** 180-day dataset walk-forward
- **Daily PF:** Holdout PF 2.56 at 18 bps cost
- **Win rate:** 65%
- **DSR:** p=0.961 (PASSED)
- **Timeframes:** 1h (cross-sectional, evaluates all symbols simultaneously)
- **Hold period:** 48 bars (48h) time-stop exit
- **Universe:** Full monitored universe (requires futures eligibility, min $5M 24h volume)
- **Cost robustness:** PF > 2.3 even at 46 bps worst-case
- **Known limitations:** Cross-sectional strategy requires all symbols' data at once. Parameters are LOCKED -- do not modify without full re-validation. Requires market breadth > 0.3 to activate.
- **File:** `worker/app/setups/s9_short_reversion.py`

### S10 -- MACD Scalp
- **Status:** ACTIVE (in signal_engine.py evaluate_setups, registered in STRATEGY_REGISTRY)
- **Version:** v1.0
- **Setup type:** momentum (scalping)
- **Direction:** LONG only
- **Last validation:** 2026-02-23 validation run (50 symbols, 5m, 21 signals detected)
- **Daily PF:** No walk-forward PF available
- **DSR:** No DSR computed
- **Timeframes:** 5m only
- **Hold period:** 12 bars (1 hour) time stop
- **Universe:** Mega/large/mid cap ($5M+ 24h volume), excludes stablecoins
- **Exit config:** 1.2 ATR stop, 1.5R TP1, 2R TP2
- **Known limitations:** Adapted from equities scalper methodology (Alan McLynn). No walk-forward validation data on file. Flat-top detection can false-trigger on low-volatility assets (stablecoin filter added). Not in SETUP_IDS env default but IS in signal_engine evaluate_setups for 5m.
- **File:** `worker/app/setups/s10_macd_scalp.py`
- **Validation data:** `scripts/output/s10_validation_results.json`

### D2 -- Fade the Casino
- **Status:** PAPER
- **Version:** v1.0
- **Setup type:** contrarian (social-driven)
- **Direction:** SHORT only
- **Last validation:** No validation data available
- **Daily PF:** No validation data available
- **DSR:** No DSR computed
- **Timeframes:** 1m (real-time monitoring via Binance REST)
- **Hold period:** Configurable (paper trading positions)
- **Universe:** Symbols detected by WhaleBotPumps social alerts
- **Known limitations:** Standalone asyncio service, separate from signal_worker. Depends on social_posts table for WhaleBotPumps messages. Paper-only -- no live signals. Gates: mania, liquidity, perp listing.
- **File:** `worker/app/d2_paper_trader.py`, `worker/app/d2_signal_parser.py`
- **DB table:** `d2_cluster_events` (migration 20260308)

---

## Archived Strategies (S1-S8)

Code moved to `Archive/setups/`. These are no longer in any production path. The old backfill script (`scripts/backfill_opportunities.py`) still references S1-S8 evaluators but those imports would fail since the files are no longer in `worker/app/setups/`.

### S1 -- BB Squeeze
- **Status:** ARCHIVED
- **Version:** v2.1
- **Setup type:** volatility breakout
- **Direction:** LONG and SHORT
- **Last validation:** No walk-forward data available
- **Daily PF:** No validation data available
- **Timeframes:** Multi-timeframe
- **Known limitations:** Retail-crowded pattern. Fail-fast exit logic added in v2.1. Superseded by D1 which targets the same volatility compression thesis with better universe filtering.
- **STRATEGY_REGISTRY status:** legacy
- **File:** `Archive/setups/s1_bb_squeeze.py`

### S2 -- Golden Cross Anticipation
- **Status:** ARCHIVED
- **Version:** v1.4
- **Setup type:** trend following
- **Direction:** LONG and SHORT
- **Last validation:** No walk-forward data available
- **Daily PF:** No validation data available
- **Timeframes:** Multi-timeframe
- **Known limitations:** Retail-crowded pattern (golden cross). BTC shock filter added in v1.4. 24h time stop. Requires SMA200 warmup.
- **STRATEGY_REGISTRY status:** legacy
- **File:** `Archive/setups/s2_golden_cross.py`

### S3 -- RSI Divergence Reversal
- **Status:** ARCHIVED
- **Version:** v1.0
- **Setup type:** reversal
- **Direction:** LONG and SHORT
- **Last validation:** No walk-forward data available
- **Daily PF:** No validation data available
- **Timeframes:** Multi-timeframe
- **Known limitations:** No validation ever performed beyond initial backfill.
- **STRATEGY_REGISTRY status:** Not in registry
- **File:** `Archive/setups/s3_rsi_divergence.py`

### S4 -- VWAP Reversion
- **Status:** ARCHIVED (was disabled even before archiving)
- **Version:** v1.0
- **Setup type:** mean_reversion
- **Direction:** LONG and SHORT
- **Last validation:** No walk-forward data available
- **Daily PF:** No validation data available
- **Timeframes:** Intraday only (1m, 5m, 15m, 1h)
- **Known limitations:** Disabled in backfill evaluator map ("S4 disabled"). VWAP resets daily, limiting applicability.
- **STRATEGY_REGISTRY status:** Not in registry
- **File:** `Archive/setups/s4_vwap_reversion.py`

### S5 -- Support/Resistance Breakout
- **Status:** ARCHIVED
- **Version:** v1.0
- **Setup type:** breakout
- **Direction:** LONG and SHORT
- **Last validation:** No walk-forward data available
- **Daily PF:** No validation data available
- **Timeframes:** Multi-timeframe
- **Known limitations:** Pivot cluster detection quality unvalidated.
- **STRATEGY_REGISTRY status:** Not in registry
- **File:** `Archive/setups/s5_sr_breakout.py`

### S6 -- Trend Continuation Pullback
- **Status:** ARCHIVED
- **Version:** v1.0
- **Setup type:** trend following (pullback)
- **Direction:** LONG and SHORT
- **Last validation:** Appeared in backfill analysis (excluded from ex-S6 portfolio metrics, suggesting poor performance)
- **Daily PF:** No validation data available (metrics computed excluding S6 suggest it dragged portfolio down)
- **Timeframes:** Multi-timeframe
- **Known limitations:** Backfill analysis scripts specifically compute "ex-S6" metrics, indicating S6 hurt aggregate performance. Superseded by S8 which applies stricter filters.
- **STRATEGY_REGISTRY status:** legacy
- **File:** `Archive/setups/s6_trend_pullback.py`

### S7 -- Momentum Breakout (Donchian)
- **Status:** ARCHIVED
- **Version:** v1.0
- **Setup type:** trend following (breakout)
- **Direction:** LONG and SHORT
- **Last validation:** Appeared in backfill analysis
- **Daily PF:** No validation data available
- **Timeframes:** Multi-timeframe
- **Known limitations:** Donchian breakout is one of the oldest trend-following signals. No evidence of edge after fees in crypto.
- **STRATEGY_REGISTRY status:** Not in registry
- **File:** `Archive/setups/s7_momentum_breakout.py`

### S8 -- MTF Momentum Pullback
- **Status:** ARCHIVED
- **Version:** v1.0
- **Setup type:** trend following (filtered pullback)
- **Direction:** LONG and SHORT
- **Last validation:** Phase 1 validated on 30-day dataset (3,262 opportunities). ABCDE4/no_TP filter combo: val PF 1.468, 13.9 trades/day.
- **Daily PF:** Val PF 1.468 (ABCDE4/no_TP)
- **Timeframes:** 1h only (15m/4h/1d disabled by Filter A)
- **Known limitations:** S6 derivative with extreme selectivity. Strong EMA slope (|slope| > 0.10), stop width capped at 4% of entry. TP disabled -- trail handles all exits. Short validation window (30 days).
- **STRATEGY_REGISTRY status:** Not in registry
- **File:** `Archive/setups/s8_mtf_momentum.py`

---

## Summary Table

| ID  | Name                  | Status   | Direction | Timeframe | Daily PF | DSR     | In SETUP_IDS |
|-----|-----------------------|----------|-----------|-----------|----------|---------|--------------|
| D1  | Dormancy-to-Ignition  | ACTIVE   | LONG      | 1h        | 2.41     | --      | Yes          |
| U1  | Unicorn Ignition      | ACTIVE   | LONG      | 5m, 15m   | --       | --      | Yes          |
| S9  | Short Reversion       | ACTIVE   | SHORT     | 1h (XS)   | 2.56*    | 0.961   | Yes          |
| S10 | MACD Scalp            | ACTIVE   | LONG      | 5m        | --       | --      | No**         |
| D2  | Fade the Casino       | PAPER    | SHORT     | 1m        | --       | --      | No           |
| S1  | BB Squeeze            | ARCHIVED | BOTH      | Multi     | --       | --      | No           |
| S2  | Golden Cross          | ARCHIVED | BOTH      | Multi     | --       | --      | No           |
| S3  | RSI Divergence        | ARCHIVED | BOTH      | Multi     | --       | --      | No           |
| S4  | VWAP Reversion        | ARCHIVED | BOTH      | Intraday  | --       | --      | No           |
| S5  | S/R Breakout          | ARCHIVED | BOTH      | Multi     | --       | --      | No           |
| S6  | Trend Pullback        | ARCHIVED | BOTH      | Multi     | --       | --      | No           |
| S7  | Momentum Breakout     | ARCHIVED | BOTH      | Multi     | --       | --      | No           |
| S8  | MTF Momentum          | ARCHIVED | BOTH      | 1h        | 1.47*    | --      | No           |

\* Holdout PF, not average daily PF
\** S10 is evaluated via signal_engine.evaluate_setups() for 5m bars but is not in the SETUP_IDS worker env var

---

## Notes

1. **Equities strategies** (EQ_TSMOM, EQ_XSMOM, EQ_REV1A/B, EQ_VPD, EQ_ATC, EQ_NVDA_MACRO, EQFP) are registered in STRATEGY_REGISTRY but are a separate domain. They use `equities_sim_runner.py` and `equities_opportunities` table. Not covered in this triage.

2. **stop_policy_registry.py** references KNOWN_SETUP_IDS = ["S1", "S2", "S6", "S9", "S10", "U1", "D1"] (worker version) and adds "D2" in the api version. Legacy IDs are kept for simulation compatibility with historical opportunities.

3. **Backfill infrastructure:** The old `backfill_opportunities.py` references S1-S8 evaluators from `Archive/setups/`. D1 has its own dedicated `backfill_d1_opportunities.py` and `sim_d1_matrix.py`.
