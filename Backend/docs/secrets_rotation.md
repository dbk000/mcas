# Secrets Rotation Procedure

## Inventory of Secrets

| Secret | Location | Used By | Rotation Impact |
|--------|----------|---------|-----------------|
| `API_KEY` | `.env` | API auth (`api/app/main.py` via `X-API-Key` header) | Frontend must update its stored key; all API calls fail until updated |
| `POSTGRES_PASSWORD` | `.env` (also embedded in `DATABASE_URL`) | All services via `env_file: .env` | Must update both `POSTGRES_PASSWORD` and `DATABASE_URL` in `.env`; requires `ALTER USER` in Postgres + full stack restart |
| `DATABASE_URL` | `.env` | All services (connection string) | Contains password — rotate alongside `POSTGRES_PASSWORD` |
| `TELEGRAM_API_ID` | `.env` | `social_collector` (Telegram channel ingestion) | Collector restarts; session file may need re-auth |
| `TELEGRAM_API_HASH` | `.env` | `social_collector` (Telegram channel ingestion) | Collector restarts; session file may need re-auth |
| `DISCORD_USER_TOKEN` | `.env` | `social_collector` (Discord selfbot collector) | Collector restart only |
| `DISCORD_BOT_TOKEN` | `.env` | `social_collector` (Discord bot fallback) | Collector restart only |
| `REDDIT_CLIENT_ID` | `.env` | `social_collector` (Reddit ingestion) | Collector restart only |
| `REDDIT_CLIENT_SECRET` | `.env` | `social_collector` (Reddit ingestion) | Collector restart only |
| `REDDIT_USERNAME` | `.env` | `social_collector` (Reddit auth) | Collector restart only |
| `REDDIT_PASSWORD` | `.env` | `social_collector` (Reddit auth) | Collector restart only |
| `DUNE_API_KEY` | `.env` | `onchain_collector` (Dune Analytics queries) | Collector restart only |
| `ANTHROPIC_API_KEY` | `.env` | `social_collector` Brain 2 (LLM analysis) | Collector restart; Brain 2 degrades gracefully (skips if missing) |
| `CLOUDFLARE_API_TOKEN` | `~/.env.cloudflare` + `~/.bashrc` | CF Pages deploy helper (`scripts/cf-deploy.sh`) | Update both locations; does not affect running containers |
| Telegram session file | Docker volume `social_telegram_sessions` | `social_collector` | Tied to `TELEGRAM_API_ID/HASH`; re-auth required if API credentials change |

## .env Security Status

- `.env` is in `Backend/.gitignore` (single entry)
- `.env` and `.env.*` are in root `.gitignore` with recursive `**/.env` patterns
- `.env.example` is explicitly un-ignored (`!.env.example`, `!**/.env.example`)
- No secrets are hardcoded in `docker-compose.yml` — all use `env_file: .env` or `${VAR:-}` substitution

## Rotation Steps

### API Key Rotation

The API key authenticates all `/v1/*` requests via the `X-API-Key` header, validated with `hmac.compare_digest()`.

1. Generate new key:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
2. Update `.env` on the VPS (`/srv/mcas/Backend/.env`):
   ```bash
   # Edit API_KEY= line
   nano /srv/mcas/Backend/.env
   ```
3. Restart API container:
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps api
   ```
4. Update the frontend's API key configuration. The frontend calls the API with this key — verify after rotation by checking the browser network tab for 401 responses.
5. Verify:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" \
     -H "X-API-Key: <NEW_KEY>" \
     https://mcasui.com/v1/health
   # Should return 200
   ```

### Database Password Rotation

The Postgres password appears in two places in `.env`: `POSTGRES_PASSWORD` and inside `DATABASE_URL`.

1. Generate new password:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(24))"
   ```
2. Change password inside Postgres first (while old password still works):
   ```bash
   docker exec backend-postgres-1 psql -U trading -d trading -c \
     "ALTER USER trading PASSWORD 'NEW_PASSWORD_HERE';"
   ```
3. Update `.env` on the VPS — both variables:
   ```
   POSTGRES_PASSWORD=NEW_PASSWORD_HERE
   DATABASE_URL=postgresql://trading:NEW_PASSWORD_HERE@postgres:5432/trading
   ```
4. Restart all services that connect to Postgres (effectively everything except Caddy, Redis, and cpu_collector):
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps \
     api worker ingestor indicator_worker_0 indicator_worker_1 \
     signal_worker_0 signal_worker_1 unicorn_pipeline ohlcv_persister \
     social_collector derivatives_collector onchain_collector \
     equities_pipeline equities_strategy_worker equities_task_worker \
     d2_paper_trader expired_opp_exit_settler
   ```
5. Verify:
   ```bash
   docker exec backend-postgres-1 psql -U trading -d trading -c "SELECT 1;"
   docker logs backend-worker-1 --tail 5  # check for connection errors
   ```

### Telegram Credentials Rotation

Telegram API ID and hash are tied to a registered Telegram application (my.telegram.org). Rotating means creating a new app or regenerating the hash.

1. Go to https://my.telegram.org and generate new API credentials
2. Update `.env`:
   ```
   TELEGRAM_API_ID=NEW_ID
   TELEGRAM_API_HASH=NEW_HASH
   ```
3. Delete the old session file (will require re-authentication):
   ```bash
   docker volume rm backend_social_telegram_sessions
   ```
4. Restart the social collector:
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps social_collector
   ```
5. Monitor logs for re-auth prompt:
   ```bash
   docker logs -f backend-social_collector-1
   ```

### Discord Token Rotation

1. Generate a new token from the Discord developer portal (bot) or extract a new user token
2. Update `.env`:
   ```
   DISCORD_USER_TOKEN=NEW_TOKEN
   # or DISCORD_BOT_TOKEN=NEW_TOKEN
   ```
3. Restart:
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps social_collector
   ```

### Reddit Credentials Rotation

1. Go to https://www.reddit.com/prefs/apps and regenerate the app secret
2. Update `.env`:
   ```
   REDDIT_CLIENT_ID=NEW_ID
   REDDIT_CLIENT_SECRET=NEW_SECRET
   ```
3. Restart:
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps social_collector
   ```

### Dune API Key Rotation

1. Generate a new key at https://dune.com/settings/api
2. Update `.env`:
   ```
   DUNE_API_KEY=NEW_KEY
   ```
3. Restart:
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps onchain_collector
   ```

### Anthropic API Key Rotation

1. Generate a new key at https://console.anthropic.com/settings/keys
2. Update `.env`:
   ```
   ANTHROPIC_API_KEY=NEW_KEY
   ```
3. Restart:
   ```bash
   cd /srv/mcas/Backend && docker compose -f docker-compose.yml up -d --no-deps social_collector
   ```

### Cloudflare API Token Rotation

This token is used only for deploy status checks, not by any container.

1. Generate a new token at https://dash.cloudflare.com/profile/api-tokens
2. Update both locations:
   ```bash
   # Update ~/.env.cloudflare
   echo "CLOUDFLARE_API_TOKEN=NEW_TOKEN" > ~/.env.cloudflare

   # Update ~/.bashrc export line
   # Find and replace the CLOUDFLARE_API_TOKEN= line
   ```
3. Source the new value: `source ~/.bashrc`
4. Verify: `./scripts/cf-deploy.sh --status`

## Audit Checklist

- [ ] No secrets in git history: `git log -p --all -S 'SUSPECT_VALUE' -- '*.env' '*.yml' '*.py'`
- [ ] `.env` not committed: `git ls-files .env` (should return empty)
- [ ] `.gitignore` covers `.env` and `.env.*` (root `.gitignore` has recursive patterns)
- [ ] No secrets hardcoded in `docker-compose.yml` (all use `env_file` or `${VAR:-}`)
- [ ] `API_KEY` is 64+ hex chars (32 bytes): `wc -c <<< "$API_KEY"` should be 65 (64 + newline)
- [ ] `POSTGRES_PASSWORD` matches in both `POSTGRES_PASSWORD` and `DATABASE_URL`
- [ ] Telegram session volume is not bind-mounted to a git-tracked path
- [ ] `~/.env.cloudflare` is not world-readable: `ls -la ~/.env.cloudflare`

## Schedule

- **Quarterly:** Rotate `API_KEY`, `POSTGRES_PASSWORD`
- **On suspected exposure:** Rotate all affected secrets immediately
- **On team member departure:** Rotate all secrets
- **On Telegram/Discord/Reddit re-auth:** Rotate the respective platform credentials
- **After any `.env` accidentally committed:** Rotate ALL secrets (assume full exposure), then `git filter-branch` or `git-filter-repo` to scrub history
