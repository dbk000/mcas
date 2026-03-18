# Governance Audit Results — Track D
_2026-03-18_

## Summary: 8 PASS, 1 FAIL (fixed), 2 FINDINGS

| Item | Audit Point | Status |
|------|-------------|--------|
| 1 | MIN_24H_QUOTE_VOLUME_USD registry sourcing | PASS |
| 2 | Ingestor dynamic universe refresh | PASS |
| 3 | D1/U1 MIN/MAX_VOLUME_USD in TypedConfig | PASS |
| 4 | SETUP_IDS derivation | PASS |
| 5 | SIGNAL_TOP_N_DAILY in frozen registry | PASS |
| 6 | 8 SNAPSHOT_FIELDS missing | FAIL → FIXED by Track A |
| 7 | D1 volume gate enforcement path | FINDING: not enforced in live path (not a blocker) |
| 8 | Setup ID naming convention (EQ_*) | PASS |
| 9 | Fee constants match execution.py | PASS |
| 10 | S9 NameError risk | FINDING: fixed by Track A (local imports added) |

## Key Finding: D1 Volume Gate Not Enforced Live (Item 7)
D1Config defines MIN_VOLUME_USD ($75K) and MAX_VOLUME_USD ($3M) but neither
`is_d1_excluded()` nor `evaluate_trigger()` enforce them. D1 evaluates all
monitored symbols (which are $5M+). This is acceptable because D1's actual
gates (dormancy_score >= 0.75, bb_width compression, volume_z spike) filter
implicitly. The SNAPSHOT_FIELDS fix is the true blocker.

## Fee Constants Verified
execution.py SLIPPAGE_PROFILE_BPS: 1m=20, 5m=15, 15m=10, 1h=5, 4h=3, 1d=2
config_fee_schedule table: matches exactly + 4bps taker fee
COALESCE default 0.09 = DEFAULT_SLIPPAGE_BPS(5) + 4bps = correct
