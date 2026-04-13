# A4 — Social Backfill Task Ledger

**Source audit:** `/home/darren/Desktop/MCAS/Backend/scripts/output/social_coverage_audit_20260412_180000.md`
**Produced by:** A4 (coverage audit)
**Consumer:** A2 (social → C-series regression), user (for paid endpoint decisions)

One line per gap. Format: `CHANNEL | WINDOW | SOURCE | SCRIPT | EST_ROWS | EST_WALL | STATUS`.

---

## FIXABLE via free re-ingest — A2 should not run D2 until these complete

1. `CHANNEL: NEWS | WINDOW: 2026-02-20 → 2026-04-06 (46d) | SOURCE: CryptoPanic free API /v1/posts?public=true + CoinDesk/Cointelegraph public RSS archives | SCRIPT: Backend/scripts/backfill_news_crypto_panic.py (to write) | EST_ROWS: 25,000-40,000 | EST_WALL: 2-4h | STATUS: unassigned`

2. `CHANNEL: REDDIT | WINDOW: 2026-02-20 → 2026-04-06 (46d) | SOURCE: PullPush.io /reddit/search/submission?subreddit=CryptoCurrency&after=X&before=Y (+ CryptoMarkets, Bitcoin, altcoin) | SCRIPT: Backend/scripts/backfill_reddit_pullpush.py (to write) | EST_ROWS: 5,000-8,000 | EST_WALL: 1-2h | STATUS: unassigned. Partial yield expected (PullPush coverage ~50-70% of historical).`

3. `CHANNEL: WHALE | WINDOW: 2026-02-20 → 2026-03-08 (17d) | SOURCE: telegram channel history re-scrape (@WhaleBotAlerts, @WhaleBotPumps, @WhaleBotRektd) via existing telegram client | SCRIPT: Backend/scripts/rescrape_telegram_whale_history.py (to write) | EST_ROWS: 3,000-5,000 | EST_WALL: 30-60 min | STATUS: unassigned. Pre-check: confirm channels retain messages past 7 weeks before writing script.`

4. `CHANNEL: DISCORD | WINDOW: 2026-03-13 → 2026-03-17 (5d) + 2026-03-27 → 2026-03-28 (2d) | SOURCE: discord bot channel.history() on servers/channels where bot remained in-server | SCRIPT: Backend/scripts/rescrape_discord_bot_history.py (to write) | EST_ROWS: 15,000-25,000 | EST_WALL: 30-60 min | STATUS: unassigned. Contingent on bot still being authorised on target servers.`

## FIXABLE via paid endpoint — requires user approval

5. `CHANNEL: WHALE-on-chain | WINDOW: 2026-02-20 → 2026-03-08 (17d) | SOURCE: whale-alert.io historical API paid plan (~$30/mo) | SCRIPT: Backend/scripts/backfill_whalealert_paid.py (to write if approved) | EST_ROWS: 500-1,000 structured whale transfers | EST_WALL: 30 min | STATUS: blocked — awaiting user decision. Only needed if #3 fails.`

## STRUCTURAL — no backfill possible, A2 must adapt scope

6. `STRUCTURAL: drop COMMUNITY (discord + live telegram) from A2 scope for opps with created_ts < 2026-03-08. Affects ~30,355 opps (64.6% of batch). Alternative: narrow A2 study period to 2026-03-08 → 2026-04-06 for COMMUNITY-dependent categories only, keep full window for NEWS/WHALE/REGULATORY/MONETARY/GEOPOLITICAL/STRUCTURAL (post-backfill).`

7. `STRUCTURAL: REDDIT historical window 2025-12-04 → 2026-02-19 already has 78 days of coverage in social_posts. Post-backfill via PullPush will likely yield 30-50% of Epoch 1 density — treat as degraded coverage, document in A2 write-up.`

8. `STRUCTURAL: DISCORD pre-2026-03-08 (entire Epoch 1) — no recovery path (bot not in-server, Discord TOS blocks mass scrape). Drop from A2 for those opps unconditionally.`

---

## Total A2 unblocking

After #1, #2, #3, #4: NEWS/REDDIT/WHALE categories regain full 2025-12-04 → 2026-04-06 coverage. COMMUNITY remains epoch-restricted but that's a documented scope narrowing, not a contamination.

**Estimated total backfill wall time (parallel execution):** ~4h real time if the four scripts run concurrently and none hit rate limits.
**Estimated total backfill wall time (serial, one operator):** ~6-7h.

Author writes the scripts. A2 waits. User approves #5 or defers it.
