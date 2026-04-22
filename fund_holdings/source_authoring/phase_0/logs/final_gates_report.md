# Final Gates Report

Bible: `/home/darren/Desktop/MCAS/fund_holdings/ground_truth/fund_bible_v1.xlsx`

## §7.1 Schema + forbidden strings
  PASS

## §7.3 Holdings_Feed_URL distinctness
  All 806 URLs are distinct.
  PASS

## §7.4 Coverage
  Total data rows across sheets: 806
  Overview Total matches sheets: 806
  ✓ Sponsor: 100.0% populated
  ✓ Exchange: 100.0% populated
  ✓ Vehicle_Type: 100.0% populated
  ✓ Replication_Method: 100.0% populated
  ✓ Rebalance_Frequency_Prospectus: 100.0% populated
  ✓ Holdings_Feed_URL: 100.0% populated
  ✓ Holdings_Feed_Format: 100.0% populated
  ⚠ Column Notes: 100.0% null (sparse, but >30%)
  PASS

## §7.5 Sanity
  Σ AUM_USD = $9,207,583,735,200
  ICI total ≈ $11,000,000,000,000
  Ratio: 83.7%
  ✓ AUM within tolerance
  ✓ No duplicate tickers (806 unique)
  PASS

## §7.2 SKIPPED (--skip-content-verify)

## Overall

✅ PASS
