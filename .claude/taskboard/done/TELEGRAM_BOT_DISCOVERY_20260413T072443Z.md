# TELEGRAM_BOT_DISCOVERY — DONE

**Completed:** 2026-04-13 07:24 UTC
**Mode:** read-only grep + file inspection (no sub-agents, no DB, no docker exec)

## Output

`/home/darren/Desktop/MCAS/Backend/scripts/output/telegram_bot_audit_20260413T072443Z.md`

## Key findings

1. **No outbound Telegram control bot exists.** All Telegram code is *inbound* — Telethon (MTProto user-client) polling third-party crypto channels in the `social_collector` service. Zero handlers, zero bot token, zero chat-id auth.
2. **Library landscape is empty for control bots.** No `python-telegram-bot`, `aiogram`, or `telebot`. Existing Telethon should NOT be repurposed (user-account, brittle, personal identity).
3. **Phase E modules referenced by spec also don't exist:** `Backend/governance/regime/enforcer.py` and `Backend/governance/risk_limits.py` are absent. `governance/regime/` contains only `keyword_clusters.py` + JSON tables.
4. **Override Redis keys are unread.** `max_concurrent_override` and `live_trading_enabled` have 0 consumers in the codebase. Bot can write them but they will be inert until cap-aware selector and autotrader runners read them.
5. **Existing FastAPI `/emergency/*` endpoints** (`api/app/routers/emergency.py`) provide the canonical kill/pause/resume/status pattern and can serve as the implementation backbone for `/kill_all`.
6. **No command-name collisions** — all 7 proposed commands are greenfield.

## Effort to unblock Phase A

~2 hours: create bot via @BotFather, add token + admin chat IDs to `.env`, `pip install python-telegram-bot==21.*`, write `Backend/governance/telegram/control_bot.py` (~150 LOC, 7 handlers + admin_only decorator), add `telegram_control_bot` service to docker-compose modelled on `collectors`, deploy.

## Decision needed

Whether to ship the bot before or after Phase E modules. Recommendation: **ship bot now with canonical Redis-key stubs** for `/regime_shock`/`/regime_clear`, swap to `enforcer.set_state()` later — no command-surface churn, immediate operator-control coverage for `/status`, `/set_cap`, `/live_trading_enable`/`disable`, `/kill_all` (latter via existing emergency endpoint).
