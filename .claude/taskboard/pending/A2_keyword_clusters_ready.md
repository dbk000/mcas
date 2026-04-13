STATUS: READY
MODULE: Backend/governance/regime/keyword_clusters.py
IMPORT: from Backend.governance.regime.keyword_clusters import CLUSTERS, match_categories, velocity, baseline_velocity, velocity_ratio, hourly_counts
CATEGORIES: GEOPOLITICAL, MONETARY, REGULATORY, STRUCTURAL, SOCIAL_WHALE, SOCIAL_NEWS, SOCIAL_COMMUNITY, SOCIAL_REDDIT
CONSUMERS:
  - A1: Backend/scripts/label_shock_events.py (macro clusters for regime Phase 1)
  - A2: Backend/scripts/discover_social_safe_filters.py (D2 — macro + SOCIAL_*)
  - A3 (optional): indicator SAFE filter feature enrichment — not required
  - future: governance/signal_worker.py for live C-series social features
PARITY:
  - Compiled regex source strings for GEOPOLITICAL / MONETARY / REGULATORY / STRUCTURAL are byte-identical to validate_regime_thesis.KEYWORD_PATTERNS.
  - Spot-check: 3/3 representative strings (one per seed-JSON trigger category REG/MON/GEO from shock_events_binance_v1_20260412_155550.json) produce identical category sets via match_categories() vs validate_regime_thesis compiled pattern.
TESTS:
  - 13/13 passing via `python3 -m pytest Backend/tests/test_keyword_clusters.py -v` (Python 3.12, host-side run). File is pytest-compatible AND directly runnable via `python3 Backend/tests/test_keyword_clusters.py`.
  - Coverage: positive match per category (6 tests — 4 macro + 2 social), 2 negatives (innocuous + empty), velocity rate math, velocity_ratio spike>2×, empty-baseline-is-zero, unknown-category raises, hourly_counts bucket alignment.
NOTES:
  - Social categories are prefixed `SOCIAL_` to avoid shadow-collision with any future macro addition of the same stem (documented in module docstring). Macro names kept unprefixed so downstream A1 code can import them verbatim.
  - `social_features.py` categorises posts by *channel name* (not content). The SOCIAL_* regexes here are a text-based fallback that matches on channel tokens + polarity words lifted from `compute_whale_polarity`. Channel-level categorisation via `categorize_channel()` remains the authoritative path for the live worker — the regex path is for scanning raw post text when channel field is absent.
  - `velocity_ratio` exposes a `baseline_floor` arg (default 0.5) that replicates validate_regime_thesis's `max(b, 0.5)` floor exactly. `baseline_velocity` does not floor — callers who want honest "no-baseline-evidence" reporting get zero.
  - Half-open intervals `[start, end)` throughout (CLAUDE.md §2).
  - Pure functions: no DB, no network, no clock reads. Safe to call from signal_worker hot path.
  - No commits made (per sprint rules). Files on disk only.
