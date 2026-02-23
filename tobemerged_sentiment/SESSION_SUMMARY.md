# MCAS_dev2 Session Summary — 2026-02-19

## What this clone was for

Multiple Claude Code sessions ran against this dev2 clone today to build the **social data pipeline** — a new subsystem that collects crypto social media data, extracts features, and feeds them into the signal pipeline. A Telegram backfill script was also added at the end.

---

## What was built (and its current state)

### 1. Social Collector Service — FULLY BUILT, NOT YET DEPLOYED
`Backend/social_collector/` — brand new Docker service with:

- **4 live collectors:**
  - `collectors/telegram.py` — Telethon-based, listens to 25 public channels (event-driven, not polled)
  - `collectors/reddit.py` — PRAW-based, polls 18 crypto subreddits every 15 min (needs Reddit API creds)
  - `collectors/rss.py` — feedparser, polls 8 crypto news RSS feeds every 5 min (no auth needed)
  - `collectors/coingecko.py` — polls CoinGecko trending endpoint every 30 min (free, no auth)

- **Brain 1** (`brain1.py`) — hourly aggregation at :05 past each hour. Computes: mention_count, unique_authors, platform_count, engagement_sum, mention_velocity, mention_acceleration, author_novelty_ratio, first_mention, mention_volume_ratio (social-volume divergence z-score)

- **Brain 2** (`brain2.py`) — event-triggered LLM extraction at :10 past each hour. Detects anomalies (volume_z > 3, first_mention, high author velocity, cross-platform). Calls Claude Haiku to extract 13 structured dimensions (catalyst_type, cascade_stage, coordination_probability, etc.). Cost-controlled: 100 calls/hour max, 4h dedup, circuit breaker.

- **Symbol matcher** (`symbol_matcher.py`) — 4-tier matching: cashtags ($BTC) → unambiguous tickers (DOGE) → ambiguous tickers with crypto context (SUN, WIN) → full names (≥5 chars). Uses `symbol_aliases` DB table.

- **Orchestrator** (`collector.py`) — main entry point, spawns all collector threads + Brain 1/2 schedulers

- **25 Telegram channels** configured in `telegram_channels.json` covering whale alerts, exchange listings, news, on-chain analytics, security intel, alpha, and social analytics

### 2. Database Migration — WRITTEN, ALREADY APPLIED ON PROD
`Backend/docs/db/migrations/20260219_add_social_tables.sql`

Creates 4 tables:
- `social_posts` — raw posts with symbol arrays, deduped by (source, source_id)
- `social_features_1h` — hourly aggregated features per symbol (Brain 1 output)
- `social_signals` — sparse LLM-extracted signal vectors (Brain 2 output)
- `symbol_aliases` — ticker/name/cashtag → symbol mappings with ambiguity flags

### 3. Symbol Aliases Population — WRITTEN AND RAN
`Backend/scripts/populate_symbol_aliases.py`
- Fetches all USDT pairs from Binance exchange info
- Inserts ticker + cashtag aliases, marks ~30-40 ambiguous tickers
- **Already executed on prod** — symbol_aliases table is populated

### 4. Reddit + News Historical Backfill — WRITTEN AND RAN
`Backend/scripts/backfill_social_posts.py` (866 lines)
- Fetches 365 days of Reddit posts via Arctic Shift/PullPush (free, no auth)
- Fetches crypto news via CryptoCompare API (free)
- Matches symbols, inserts into social_posts
- Materializes social_features_1h for entire window
- Computes mention_volume_ratio in second pass

**Results in production DB:**
| Source | Posts | Date Range |
|--------|-------|------------|
| news_historical | 112,892 | 2025-07-07 → 2026-02-19 |
| reddit_historical | 38,563 | 2025-02-19 → 2026-02-19 |

- 103,362 feature rows across 414 symbols in social_features_1h
- 93,057 rows have mention_volume_ratio (90% coverage)

### 5. Telegram Historical Backfill — WRITTEN, NEEDS FIRST-AUTH THEN RUN
`Backend/scripts/backfill_telegram.py`
- Uses Telethon to iterate all 25 channels back 365 days
- Same symbol matching as Reddit/news backfill
- Writes in batches of 500, progress logging every 30s
- Source label: `telegram_historical`
- **Blocked on:** interactive first-auth (Telegram sends code to phone)

### 6. docker-compose.yml — MODIFIED
Added `social_collector` service definition + `social_telegram_sessions` volume.

### 7. .env — MODIFIED
Added:
```
TELEGRAM_API_ID=31396919
TELEGRAM_API_HASH=2a80770764355d5d6c0e87736584f8fb
```

### 8. Signal Research Scripts (pre-existing from earlier sessions)
- `Backend/scripts/signal_genesis_alt4/` — alternative signal discovery results (timeseries, long-mean-reversion, event-driven)
- `Backend/scripts/signal_genesis_ignition/` — primary ignition model research script + results
- `Backend/scripts/sim_run_jarvis.py` — simulation analysis/attribution tool (1,322 lines), not yet run

### 9. Spec Documents
- `SOCIAL_PIPELINE_SPEC.md` — master architecture doc for the two-brain social pipeline
- `SOCIAL_BACKFILL_SPEC.md` — detailed backfill strategy and script spec
- `crypto-chatter-build-spec.md` — step-by-step build plan for the social_collector service

---

## What's done vs what's pending

| Item | Status |
|------|--------|
| DB migration (4 social tables) | Applied on prod |
| symbol_aliases populated | Done |
| Reddit + news 365-day backfill | Done, 151K posts in DB |
| social_features_1h materialized | Done, 103K rows |
| Social collector service code | Fully written |
| Social collector deployed to prod | **NOT YET** — needs compose update + build |
| Telegram first-auth | **NOT YET** — needs interactive terminal |
| Telegram 365-day backfill | **NOT YET** — needs first-auth, then run script |
| Re-materialize features after TG backfill | **NOT YET** — run after TG backfill completes |
| Reddit live collector | Needs Reddit API creds (not urgent, backfill covers 365 days) |

---

## Immediate next steps for prod Claude

1. **Merge files** from this folder into the main repo per MERGE_MANIFEST.md
2. **Deploy social_collector** — update prod docker-compose.yml, build, start
3. **Telegram first-auth** — Darren does this interactively
4. **Run Telegram backfill** — `backfill_telegram.py --days 365`
5. **Re-materialize social_features_1h** — to incorporate Telegram data
6. **Verify live collectors** are running (RSS + CoinGecko will work immediately, Telegram after auth, Reddit whenever creds are added)
