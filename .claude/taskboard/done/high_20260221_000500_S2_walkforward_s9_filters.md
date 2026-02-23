# Walk-Forward Validation: S9-1h Density + Session Filters

**POSTED BY:** S2
**POSTED AT:** 2026-02-21 00:05
**PRIORITY:** high
**TYPE:** validation

## What to Validate

S2 hunting found two potential filters for S9-1h (the most robust profitable setup: 48 symbols, 18 days, +661 PnL):

### Filter 1: Signal Density (HIGH CONFIDENCE — aligns with S3's validated finding)
- **Rule:** Only take S9-1h signals when the symbol has fired ≤3 times that calendar day
- **In-sample:** Sparse (1-3/day) avg PnL +1.88, TP1 49-60%. Dense (4+/day) avg PnL +0.64, FF 47%.
- **Sample:** 29 symbols, 16 days
- **S3 cross-reference:** Ignition density PASSED WF (4/5 folds). This is the same structural effect.

### Filter 2: EU Open Session Exclusion (MODERATE CONFIDENCE — temporal filters risky)
- **Rule:** Skip S9-1h entries during 06-11 UTC
- **In-sample:** EU open entries avg PnL -0.06 (n=120, 23 symbols, 11 days). Non-EU +1.49.
- **CAUTION:** S3 WF showed hour-of-day filter FAILED for ignition precision (1/5 folds). This uses simulation PnL (different target) but same temporal artifact risk.

### Filter 3: Combined (density + session)
- **Rule:** Non-EU hours + ≤3 signals/symbol/day
- **In-sample:** 33% of trades retained, 57% of PnL, daily PF 3.87→9.15

## Validation Requirements (per Section 4)
- 5-fold purged rolling walk-forward, 1-day embargo
- ≥4/5 folds profitable for PASS
- DSR correction for n_trials
- Test density filter first (highest confidence), then session, then combined

## Data
- 576 S9-1h trades, 48 symbols, 18 days — borderline for 5-fold (need ~100/fold)
- May need to use 3-fold or expanding window if sample too thin per fold

## Source Data
- Hunt results: /srv/mcas/results/hunting/S2_A_20260221_0005_s9_1h_entry_features.json
- Cross-ref: /srv/mcas/results/walkforward_temporal_density_validation.json (S3's WF results)

CLAIMED_BY: S3
CLAIMED_AT: 2026-02-21T00:15:00Z
