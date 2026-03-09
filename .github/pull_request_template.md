## Summary
<!-- Brief description of changes -->

## Type
- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring
- [ ] Configuration change
- [ ] Documentation

## Matrix Sanity Checklist
<!-- Required for any change that affects signal generation, evaluation, or simulation -->
- [ ] **Parameter changes:** If any setup parameter was modified, the winning config still produces the best PF in a matrix run
- [ ] **Exit config changes:** Exit config changes validated against `sim_d1_matrix.py` or equivalent
- [ ] **Indicator changes:** Walk-forward validation confirms no regression (5-fold purged rolling, DSR > 0)
- [ ] **Gate changes:** Filter rate impact measured (run with `--dry-run` or compare before/after counts)
- [ ] **OHLCV/data changes:** Warmup accounting verified (target_days + warmup_days)
- [ ] **N/A:** This PR does not affect signal generation or simulation

## Opportunity Regeneration
<!-- Required if signal generation logic changed (CLAUDE.md Section 1.4) -->
- [ ] Expired opportunities need regeneration — commands:
  ```bash
  # Provide the exact backfill commands here
  ```
- [ ] N/A — no signal-affecting changes

## Deployment
- [ ] DB migration required (run on dev first, verify, then production per CLAUDE.md Section 1.5)
- [ ] Frontend changes (auto-deploys via CF Pages on `git push origin main`)
- [ ] Backend service restart required (list services: _______________)
- [ ] No deployment action needed

## Validation Evidence
<!-- For signal-affecting changes, paste or link to validation output -->
<!-- e.g., matrix run results, walk-forward fold summaries, PF before/after -->

## Test Plan
<!-- How to verify this change works -->
- [ ] _describe verification steps_
