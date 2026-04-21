# Phase B Runbook — sector-by-sector, one session, commit between

This runbook is what the *next* CC session executes. Phase A is complete and locked.

## Before starting

```bash
cd /home/darren/Desktop/MCAS/equities_news
python3 source_authoring/verification/merge_gate.py --skip-format
# expect exit 0 — if not, Phase A is broken; do not proceed
python3 -m pytest tests/ -q
# expect 37+ passed
```

## Sector order (easiest → hardest; parallelism-proxy ordering)

Per the Phase 1.b budget section:

1. `utilities`              (143 sym, 20 countries)
2. `energy`                 (191, 20)
3. `communication_services` (238, 19)
4. `consumer_staples`       (293, 21)
5. `real_estate`            (387, 20)
6. `materials`              (359, 23)
7. `information_technology` (559, 19)
8. `consumer_discretionary` (636, 20)
9. `health_care`            (621, 19)
10. `financials`            (800, 26)
11. `industrials`           (976, 25)

## Per-sector loop (exactly this sequence, no skipping)

```
for sector in $SECTORS; do
  # 1. Read the briefing.
  cat source_authoring/phase_b/sector_briefings/${sector}.md

  # 2. Author sector.${sector}.yaml — 18-55 entries depending on sector.
  #    Use the agent harness:
  #
  #      from sector_agent import SectorAgent
  #      agent = SectorAgent("${sector}")
  #      agent.preflight()
  #      rows = agent.read_sector_sheet()
  #      agent.author_sector_yaml([...])
  #      agent.author_sub_industry_yamls({...})

  # 3. For each sub-industry the briefing flags, author
  #    registry/news_sources/sub_industries/${sector}/${sub_slug}.yaml.

  # 4. Overlay fill — one pass per row, no skips:
  #      for row in rows:
  #        agent.fill_overlay(row, ir_url=..., ...) 

  # 5. Self-verify locally.
  python3 -c "from sector_agent import SectorAgent; \
              a=SectorAgent('${sector}'); a.preflight(); \
              a.read_sector_sheet(); \
              import sys; \
              issues=a.self_verify(); \
              print('\n'.join(issues)); sys.exit(1 if issues else 0)"

  # 6. Emit enriched sheet.
  python3 -c "from sector_agent import SectorAgent; \
              a=SectorAgent('${sector}'); a.read_sector_sheet(); \
              # (overlay values reconstructed from author log at replay time)
              print(a.emit_enriched_sheet())"

  # 7. Run registry consistency across everything.
  python3 source_authoring/verification/consistency_checker.py || exit 1

  # 8. Commit + push.
  git add registry/ source_authoring/phase_b/logs/${sector}.jsonl ground_truth/bible_v1_enriched.xlsx
  git commit -m "phase_b: ${sector} — sector YAML + overlay (N symbols, M sources)"
  git push origin main
done
```

## Checkpoint contract (per the taskboard)

After EACH sector commits:
- `registry/news_sources/sectors/${sector}.yaml` exists, non-empty, schema-clean.
- `registry/news_sources/sub_industries/${sector}/*.yaml` for flagged overrides (may be zero).
- `source_authoring/phase_b/logs/${sector}.jsonl` has one line per symbol in the sector.
- `ground_truth/bible_v1_enriched.xlsx` updated with overlay columns filled for this sector's rows only (other sectors still "null" until their turn).
- `consistency_checker` exits 0.
- Commit + push happens BEFORE the next sector begins.

## Aggregate gate (after all 11 sectors)

```bash
python3 source_authoring/verification/merge_gate.py --skip-format
# If you have a network window, run full:
python3 source_authoring/verification/merge_gate.py
# This populates lineage.verified_at and broken_urls.yaml.

# 5% content audit — defer to Opus, sample 5% per sector, confirm each URL
# resolves to the claimed source. Any sector with >5% confusion rate =
# re-run that sector.

# Tag the merge.
git tag phase-b-locked
git push origin phase-b-locked
```

## Rules (copy from taskboard §2)

- Do NOT mutate Phase A files (universal.yaml, geo/*.yaml) from Phase B. Writes outside your assigned namespace are write-boundary violations — the harness will raise.
- Do NOT include sector-specific sources in geo/ (e.g. Mining.com goes in sector.materials, NOT geo.au).
- Every overlay cell must be explicit — `"null"` string for intentional empty, never a blank cell.
- Do NOT skip symbols. The author log must have one row per symbol per sector.
- Do NOT add new countries — every country is already in geo/. If a Bible country was missed in Phase A, stop and flag.

## Expected totals on completion

- Sector YAMLs:          11 files, ~250-400 entries total.
- Sub-industry YAMLs:    ~50 files (sparse).
- Overlay rows:          5,203 (one per Bible symbol).
- Author log rows:       5,203 (one per Bible symbol).
- Distinct sources in registry:    ~500 (taskboard estimate: 250-400 after dedup).

## If something goes wrong

- Script crash mid-sector: the author log is append-only so partial work is preserved.
  Resume by re-running the fill for any ticker whose latest log row pre-dates the crash.
- HTTP timeout during format validation: run `--skip-format` for the interim gate, defer
  full URL check to aggregate gate.
- Schema error on a sector YAML: `consistency_checker` will point at the file+entry. Fix
  and retry; do NOT commit partial work.

## Do NOT

- Launch 11 parallel agent sessions. Parallelism rule overridden by taskboard addendum:
  Phase B is sequential in one session.
- Skip tests between sectors. Regressions in the harness mid-run are almost impossible
  to debug later.
- Tag `phase-b-locked` before the aggregate gate (including content audit) passes.
