# BINANCE_AUTH_CHECK

**Verdict:** `NOT_READY_FOR_LIVE`
**Timestamp (UTC):** 20260413T072428Z
**Output report:** `/home/darren/Desktop/MCAS/Backend/scripts/output/binance_auth_check_20260413T072428Z.md`

## Blocking Issues
- `canWithdraw == True` (CRITICAL — key over-privileged for autotrader)
- `permissions` does not include `SPOT` (only `TRD_GRP_039` returned)

## Observed
- canTrade: True
- canWithdraw: True
- canDeposit: (see report)
- permissions: ['TRD_GRP_039']
- accountType: see report
- Key last-4: `...evVF`

## Manual Follow-up
- Verify IP whitelist on Binance console contains VPS `65.109.7.49` (not API-checkable).
- Rotate key with withdraw disabled before Monday flip.
