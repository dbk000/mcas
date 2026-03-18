"""
Telegram Collector — Polling Mode
==================================

Polls configured Telegram channels for new messages every POLL_INTERVAL_S.
Uses iter_messages(min_id=last_seen) instead of Telethon's event system,
which doesn't receive updates for this account type.

Requires first-auth by Darren (interactive session creation).
"""

import os
import re
import json
import logging
import asyncio
import threading
from datetime import datetime, timezone

import config
from db import batch_insert_posts
from symbol_matcher import SymbolMatcher

logger = logging.getLogger("telegram")

CHANNELS_FILE = os.path.join(os.path.dirname(__file__), '..', 'telegram_channels.json')

# Polling interval in seconds
POLL_INTERVAL_S = int(os.getenv("TELEGRAM_POLL_INTERVAL_S", "30"))

# Bot-alert channels that use structured message formats (e.g. "SYMBOL/USDT").
# Downstream consumers (D2 paper trader) parse these directly — bypass the
# symbol matcher which misses obscure coins not in symbol_aliases.
_BOT_ALERT_CATEGORIES = {"whale_alerts", "alt_monitor", "liquidations",
                          "pump_coordination", "technical_alerts"}

# Regex to extract "SYMBOL/USDT" from bot-alert messages
_PAIR_RE = re.compile(r'\b([A-Z0-9]{2,20})/USDT\b')


def load_channel_list() -> list[dict]:
    """Load Telegram channel list from JSON config."""
    path = os.path.abspath(CHANNELS_FILE)
    if not os.path.exists(path):
        logger.warning("telegram_channels.json not found at %s", path)
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("channels", [])


class TelegramCollector:
    def __init__(self, matcher: SymbolMatcher):
        self.matcher = matcher
        self.client = None
        self.channels = load_channel_list()
        self._loop = None
        self._thread = None
        self._running = False
        # Per-channel high-water mark: entity_id → last_seen_msg_id
        self._hwm: dict[int, int] = {}

    def start(self):
        """Start the Telegram poller if credentials are configured."""
        if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
            logger.info("Telegram collector disabled — no credentials in env "
                        "(TELEGRAM_API_ID / TELEGRAM_API_HASH)")
            return

        session_path = config.TELEGRAM_SESSION_PATH
        session_file = session_path + ".session"
        if not os.path.exists(session_file):
            logger.warning(
                "Telegram requires first-auth. Session file not found at %s. "
                "Run interactively:\n"
                "  docker compose run -it social_collector python -c \"\n"
                "    from telethon.sync import TelegramClient\n"
                "    c = TelegramClient('/app/sessions/mcas', %%s, '%%s')\n"
                "    c.start()\n"
                "    print('Auth complete')\n"
                "  \"",
                session_file,
            )
            return

        if not self.channels:
            logger.warning("Telegram: no channels configured")
            return

        logger.info("Telegram: starting poller with %d channels (interval=%ds)",
                     len(self.channels), POLL_INTERVAL_S)
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        """Run Telethon polling in its own asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._poll_loop())
        except Exception as e:
            logger.error("Telegram poller error: %s", e, exc_info=True)
        finally:
            self._loop.close()

    async def _poll_loop(self):
        """Connect to Telegram and poll channels in a loop."""
        from telethon import TelegramClient

        self.client = TelegramClient(
            config.TELEGRAM_SESSION_PATH,
            int(config.TELEGRAM_API_ID),
            config.TELEGRAM_API_HASH,
        )

        await self.client.start()
        logger.info("Telegram client connected")

        # Resolve channel entities and build lookups
        channel_infos = []  # list of (entity, username, category)
        for ch in self.channels:
            try:
                entity = await self.client.get_entity(ch["username"])
                category = ch.get("category", "")
                channel_infos.append((entity, ch["username"], category))
                logger.info("Telegram: resolved channel %s", ch["username"])
            except Exception as e:
                logger.warning("Telegram: could not resolve channel %s: %s",
                               ch["username"], e)

        if not channel_infos:
            logger.warning("Telegram: no channels resolved, poller idle")
            return

        # Initialize HWM: get latest message ID per channel to avoid replaying history
        for entity, username, _ in channel_infos:
            try:
                async for msg in self.client.iter_messages(entity, limit=1):
                    self._hwm[entity.id] = msg.id
                    logger.info("Telegram: %s HWM initialized at msg_id=%d", username, msg.id)
            except Exception as e:
                logger.warning("Telegram: failed to init HWM for %s: %s", username, e)
                self._hwm[entity.id] = 0

        logger.info("Telegram: polling %d channels every %ds", len(channel_infos), POLL_INTERVAL_S)

        cycle = 0
        while self._running:
            cycle += 1
            total_new = 0
            for entity, username, category in channel_infos:
                try:
                    new = await self._poll_channel(entity, username, category)
                    total_new += new
                except Exception as e:
                    logger.error("Telegram: poll error for %s: %s", username, e)

            logger.info("Telegram: poll cycle #%d — %d new messages from %d channels",
                         cycle, total_new, len(channel_infos))

            await asyncio.sleep(POLL_INTERVAL_S)

    async def _poll_channel(self, entity, username: str, category: str) -> int:
        """Poll a single channel for new messages since HWM. Returns count inserted."""
        min_id = self._hwm.get(entity.id, 0)
        is_bot_alert = category in _BOT_ALERT_CATEGORIES

        posts = []
        new_hwm = min_id

        async for msg in self.client.iter_messages(entity, min_id=min_id, limit=100):
            if msg.id <= min_id:
                continue

            new_hwm = max(new_hwm, msg.id)

            text = msg.text or ""
            if not text:
                continue

            # Symbol extraction: matcher first, then PAIR regex for bot-alert channels
            symbols = self.matcher.extract_symbols(text)

            if not symbols and is_bot_alert:
                pair_matches = _PAIR_RE.findall(text)
                symbols = sorted({f"{s}USDT" for s in pair_matches})

            if not symbols and not is_bot_alert:
                continue

            msg_date = msg.date
            if msg_date and msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)

            posts.append({
                "source": "telegram",
                "source_id": f"tg_{msg.id}_{entity.id}",
                "channel": username,
                "author": None,
                "title": None,
                "content": text[:config.MAX_CONTENT_LENGTH],
                "post_ts": msg_date,
                "engagement_score": msg.views or 0,
                "symbols": symbols,
                "batch_id": f"telegram_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            })

        if posts:
            batch_insert_posts(posts)
            logger.info("Telegram: %s — %d new messages (hwm %d→%d)",
                         username, len(posts), min_id, new_hwm)

        if new_hwm > min_id:
            self._hwm[entity.id] = new_hwm

        return len(posts)

    def collect_cycle(self) -> int:
        """No-op — Telegram runs its own poll loop in a thread."""
        return 0
