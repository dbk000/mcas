STATUS: READY_FOR_REVIEW
SCRIPTS:
  - Backend/scripts/backfill_social_news_cryptopanic.py
  - Backend/scripts/backfill_social_reddit_pullpush.py
  - Backend/scripts/backfill_social_whale_telegram.py
  - Backend/scripts/backfill_social_discord_gaps.py
REVIEW_PACKET: Backend/scripts/output/backfill_review_packet_20260412T211336Z.md
COMMIT_FLAG_REQUIRED: --commit (default is dry-run)
USER_ACTION: review packet, then authorise the order/sequencing of real runs
BLOCKING: A2-D2

NOTES:
- All 4 scripts default to --dry-run (no writes without --commit).
- All 4 use ON CONFLICT (source, source_id) DO NOTHING against social_posts.
- Scripts 3 (telegram) and 4 (discord) require execution inside social_collector
  container (has telethon + session file + discord token env). May require
  extending /scripts bind-mount to that container, or copying scripts into image.
- Sample mode (--sample-first-hours 24) available on all 4 scripts for spot-check.
- Resume (--resume) + state files in Backend/scripts/output/backfill_state/.
- Full expected wall: 4-7h serial, ~2-3h parallel.
- A5 scope-narrow recommendation included in review packet §7.
