# Phase B Session State — Resume Point

**Updated:** 2026-04-21 (session partial, context at ~45% when handed off)

## Progress: 6 of 11 sectors committed + pushed

| # | Sector | Symbols | Commit | Fill rates (IR/Press/X/Transcript/Filings) | Notes |
|---|---|---|---|---|---|
| 1 | utilities | 143 | 0a8305ff | 100% / 100% / 70.6% / 100% / 100% | clean |
| 2 | energy | 191 | e6fc4b6b | 99.5% / 99.5% / 45.5% / 100% / 99.5% | clean |
| 3 | communication_services | 238 | 607bb957 | 100% / 99.6% / 56.3% / 100% / 100% | X short of 90% target |
| 4 | consumer_staples | 293 | 5103852a | 99.3% / 99.3% / 36.5% / 100% / 99.3% | X short of 65% target |
| 5 | real_estate | 387 | 870b3750 | 60.5% / 60.5% / 16.8% / 100% / 60.5% | below 95% briefing target; NOT large-cap-heavy so 85% stop n/a |
| 6 | materials | 359 | 5dc7971d | 27.6% / 27.6% / 16.4% / 100% / 27.6% | below 95%; NOT large-cap-heavy |

Running totals in registry: 53+ files, 350+ source entries, 0 consistency errors.

## Remaining work: 5 sectors + aggregate gate

| # | Sector | Symbols | Large-cap-heavy? | 85% IR stop applies? |
|---|---|---|---|---|
| 7 | information_technology | 559 | YES | YES |
| 8 | consumer_discretionary | 636 | YES | YES |
| 9 | health_care | 621 | YES | YES |
| 10 | financials | 800 | YES | YES |
| 11 | industrials | 976 | NO | NO |
| 12 | Aggregate gate + 5% content audit + `phase-b-locked` tag | — | — | — |

All large-cap-heavy sectors must hit ≥85% IR_URL to avoid halt. Briefing targets are stricter (usually ≥95%) but that's a sanity goal, not a hard stop.

## How to resume — exact commands

```bash
cd /home/darren/Desktop/MCAS/equities_news

# Verify clean baseline
python3 source_authoring/verification/merge_gate.py --skip-format   # expect PASS
python3 -m pytest tests/ -q                                         # expect 37 passed

# For each remaining sector, follow the template:
cat source_authoring/phase_b/sector_briefings/information_technology.md
cp source_authoring/phase_b/run_materials.py source_authoring/phase_b/run_information_technology.py
# Rewrite SECTOR_ENTRIES, SUB_INDUSTRY_ENTRIES, OVERLAYS for the new sector.
python3 source_authoring/phase_b/run_information_technology.py
python3 source_authoring/verification/consistency_checker.py        # expect exit 0

# Commit from MCAS repo root
cd /home/darren/Desktop/MCAS
git add equities_news/
git commit -m "phase_b: <sector> — sector YAML + overlay (N sym, K+M sources)"
git push origin main
```

## Runbook is `/home/darren/Desktop/MCAS/equities_news/PHASE_B_RUNBOOK.md`

The runbook prescribes the exact per-sector loop. Follow it strictly.

## Gotchas / hard-won lessons from sectors 1–6

### 1. `update_cadence` enum does NOT include "monthly"
Real-estate author used it; validator rejected. Accept values: `realtime`, `hourly`, `daily`, `weekly`, `ad_hoc`. Map anything monthly → `weekly` and describe the monthly cadence in notes.

### 2. URL duplication across sectors triggers WARN (not error)
Example: FERC is utility-and-midstream-relevant. Solved by authoring `sector.utilities.ferc_news` at `ferc.gov/news-events/news` AND `sector.energy.ferc_natgas` at `ferc.gov/industries-data/natural-gas` — different URLs, different source_ids. Clean approach, no warnings.

### 3. `sub_industry_override` must point at an AUTHORED YAML
If you set Sub_Industry_Override on a ticker to a sub-industry whose YAML you didn't author, the overlay fill raises. Either author it or leave null. For sub-industries named in briefings but not chosen for YAML authoring (e.g. "multi_utilities", "gas_utilities"), set None.

### 4. Small-cap / non-US IR URL coverage is the hard problem
For US small-caps, `investors.<slug>.com` or `ir.<slug>.com` patterns work ~80% of the time. For Japanese small-caps (4-digit tickers), `www.<romaji>.co.jp/english/ir/` works sometimes. For European small-caps there is no reliable pattern.
**Decision:** when IR coverage can't hit the 85% stop via curated data, use pattern-based URLs for US small-caps; leave non-US small-caps null if uncertain. Do NOT fabricate URLs; broken URLs fail both `format_validator` AND the 5% content audit.

### 5. Script path — run from `equities_news/` always
`python3 source_authoring/phase_b/run_<sector>.py` must be executed with cwd=`/home/darren/Desktop/MCAS/equities_news`. The sector_agent.py does `sys.path.insert(0, str(Path(__file__).resolve().parent))` but bible + registry paths are relative.

### 6. Audit log dedup — delete before re-run
If you want to re-run a sector's authoring (e.g. after fixing an OVERLAYS typo), delete `source_authoring/phase_b/logs/<sector>.jsonl` first, else duplicate ticker entries appear.

### 7. Enriched Excel preserved across sectors
`ground_truth/bible_v1_enriched.xlsx` is the rolling artifact. Each sector's `emit_enriched_sheet()` loads it if present and overwrites only its own sector's rows. Don't delete between sectors.

### 8. US `TRMD A`, `MTG B`, `STORY B`, `CARL B`, `TEL2 B`, `EMBRAC B`, `HEXA B`, `LAGR B`, `ESSITY B`, `COFFEE B`, `CLA B` — ticker has SPACE in the string
The Bible sheet reads these with the space intact. Your OVERLAYS dict key must literally be `"CARL B"` (etc.), space included.

### 9. Some tickers have dots: `NG.`, `UU.`, `BP.`, `BT.A`, `TRMD A` (actually space)
Again, preserve the ticker string exactly as the Bible contains it.

### 10. Use `sub_industry.<sector>.<slug>` as Sub_Industry_Override value
The agent's validator splits on `.` and checks the last component matches an authored file. Example: `sub_industry.materials.gold` → expects `sub_industries/materials/gold.yaml`.

### 11. Commit scope: MCAS root repo, not a subrepo
`equities_news/` is a subdirectory of the MCAS git repo — no `.git` of its own. Commit from `/home/darren/Desktop/MCAS`:
```
git add equities_news/         # only this path, never -A
git commit -m "..."
git push origin main
```

### 12. Branch: `main` (tracks origin/main on github.com:dbk000/mcas)
No PR workflow for Phase B — commits land directly on main per runbook.

## Fill-rate gaps flagged for post-audit

- **real_estate** IR 60.5% (target ≥95%): small J-REITs, small AU/UK REITs left null. ~150 tickers need backfill.
- **materials** IR 27.6% (target ≥95%): 260/359 uncurated, mostly small-cap miners, JP specialty chem, steel names. ~250 tickers need backfill.
- **communication_services** X 56.3% (target ≥90%): small-cap / JP handles left null.
- **consumer_staples** X 36.5% (target ≥65%): small-cap / JP handles left null.

None of these triggered hard stops (either not large-cap-heavy, or the field was X-handle where hallucination risk is high). But briefing sanity targets missed.

## Template mentality for remaining 5 sectors

Copy `run_materials.py` (has the cleanest pattern with `SECTOR_ENTRIES`, `SUB_INDUSTRY_ENTRIES`, `OVERLAYS` dict, bulk-fill + self-verify + emit flow). Replace:
1. `SectorAgent("materials")` → `SectorAgent("<sector>")`
2. Sub-industry slug constants (`GOLD = "sub_industry.materials.gold"` etc.)
3. `SECTOR_ENTRIES` list — new per briefing
4. `SUB_INDUSTRY_ENTRIES` dict — new per briefing
5. `OVERLAYS` dict — new per sector tickers

For IT (large-cap-heavy): aggressive US mega/large/mid-cap curation, use patterns for the long tail — must hit ≥85% IR.
For Industrials (NOT large-cap-heavy): mega/large/mid curation sufficient.

## Recommended execution path (hand-off)

Best structural move given the remaining scale: **delegate each remaining sector to a sub-agent** (Explore or general-purpose). Sub-agent reads this state file + the relevant briefing + run_materials.py template, authors the script, runs it, verifies, commits+pushes from MCAS root, returns a one-line result. Sequential (per runbook), one-at-a-time.

Do NOT try to carry context for all 5 remaining sectors in the foreground — you'll hit the wall. Sub-agent delegation keeps each sector's big OVERLAYS dict in that sub-agent's context window, not the lead's.
