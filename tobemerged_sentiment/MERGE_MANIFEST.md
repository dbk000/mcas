# Merge Manifest — MCAS_dev2 uncommitted changes

Created: 2026-02-19
Source: MCAS_dev2 (dev repo on desktop)
Target: /srv/mcas (production repo on 65.109.7.49)

## Files and where they go

### Backend — docker-compose.yml (MODIFIED)
- `docker-compose.yml` → `Backend/docker-compose.yml`
- Key change: added `social_collector` service + `social_telegram_sessions` volume
- **CAREFUL**: Merge with existing prod compose, don't overwrite blindly

### Backend — New social_collector service (ENTIRE DIRECTORY)
- `social_collector/` → `Backend/social_collector/`
- Brand new service: Telegram, Reddit, RSS, CoinGecko collectors + Brain 1/2 pipeline
- Has its own Dockerfile, requirements.txt, and app/ directory

### Backend — DB migration
- `20260219_add_social_tables.sql` → `Backend/docs/db/migrations/`
- Creates: social_posts, social_features_1h, social_signals, symbol_aliases tables
- Idempotent (IF NOT EXISTS)

### Backend — Scripts
- `backfill_social_posts.py` → `Backend/scripts/` — Reddit + news historical backfill (ALREADY RAN, data in DB)
- `backfill_telegram.py` → `Backend/scripts/` — Telegram historical backfill (NEEDS FIRST-AUTH THEN RUN)
- `populate_symbol_aliases.py` → `Backend/scripts/` — Populates symbol_aliases table (ALREADY RAN)
- `sim_run_jarvis.py` → `Backend/scripts/` — Simulation runner
- `signal_genesis_alt4/` → `Backend/scripts/signal_genesis_alt4/` — Signal research scripts
- `signal_genesis_ignition/` → `Backend/scripts/signal_genesis_ignition/` — Ignition signal research

### Frontend — rebuilt dist (MODIFIED)
- `Frontend_dist/` → `Frontend/dist/`
- Vite rebuild with new asset hashes — just a fresh `npm run build` output
- Overwrite the existing `Frontend/dist/` directory

### Root — Specs & docs (informational, not code)
- `SOCIAL_PIPELINE_SPEC.md` → repo root
- `SOCIAL_BACKFILL_SPEC.md` → repo root
- `crypto-chatter-build-spec.md` → repo root
- `telegram_channels.json` → repo root (reference copy; canonical is inside social_collector/app/)
- `telegram_channels_report.md` → repo root
- `logging_plan.txt` → repo root

## .env additions needed on production
```
TELEGRAM_API_ID=31396919
TELEGRAM_API_HASH=2a80770764355d5d6c0e87736584f8fb
```

## Post-merge: Telegram backfill steps
1. Add social_collector service to prod compose + build
2. First-auth (interactive): `docker compose -f docker-compose.yml run -it social_collector python -c "from telethon.sync import TelegramClient; c = TelegramClient('/app/sessions/mcas', 31396919, '2a80770764355d5d6c0e87736584f8fb'); c.start(); c.disconnect()"`
3. Run backfill: `docker compose -f docker-compose.yml run --rm -it social_collector python /app/backfill_telegram.py --days 365`
