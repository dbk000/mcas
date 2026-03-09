"""
Telegram Collector
==================

Checks for credentials and session file. If configured and authenticated,
listens to configured channels. Otherwise logs a clear message and returns.

Full Telethon integration requires interactive first-auth by Darren.
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

    def start(self):
        """Start the Telegram listener if credentials are configured."""
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
                "    c = TelegramClient('/app/sessions/mcas', %s, '%s')\n"
                "    c.start()\n"
                "    print('Auth complete')\n"
                "  \"",
                session_file, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH
            )
            return

        if not self.channels:
            logger.warning("Telegram: no channels configured")
            return

        logger.info("Telegram: starting listener with %d channels", len(self.channels))
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        """Run Telethon in its own asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._listen())
        except Exception as e:
            logger.error("Telegram listener error: %s", e, exc_info=True)
        finally:
            self._loop.close()

    async def _listen(self):
        """Connect to Telegram and listen for messages."""
        from telethon import TelegramClient, events

        self.client = TelegramClient(
            config.TELEGRAM_SESSION_PATH,
            int(config.TELEGRAM_API_ID),
            config.TELEGRAM_API_HASH,
        )

        await self.client.start()
        logger.info("Telegram client connected")

        # Build channel_id → category lookup for bot-alert bypass
        channel_entities = []
        entity_category = {}  # entity.id → category string
        for ch in self.channels:
            try:
                entity = await self.client.get_entity(ch["username"])
                channel_entities.append(entity)
                entity_category[entity.id] = ch.get("category", "")
                logger.info("Telegram: resolved channel %s", ch["username"])
            except Exception as e:
                logger.warning("Telegram: could not resolve channel %s: %s",
                               ch["username"], e)

        if not channel_entities:
            logger.warning("Telegram: no channels resolved, listener idle")
            await self.client.run_until_disconnected()
            return

        @self.client.on(events.NewMessage(chats=channel_entities))
        async def handler(event):
            try:
                text = event.message.text or ""
                if not text:
                    return

                # Determine channel info
                chat_id = event.chat_id
                channel_name = ""
                if hasattr(event.chat, 'username') and event.chat.username:
                    channel_name = event.chat.username
                elif hasattr(event.chat, 'title') and event.chat.title:
                    channel_name = event.chat.title

                category = entity_category.get(chat_id, "")

                # Symbol extraction: use matcher first, fall back to pair regex
                # for bot-alert channels that use structured "SYMBOL/USDT" format
                symbols = self.matcher.extract_symbols(text)
                is_bot_alert = category in _BOT_ALERT_CATEGORIES

                if not symbols and is_bot_alert:
                    # Extract from "SYMBOL/USDT" pattern
                    pair_matches = _PAIR_RE.findall(text)
                    symbols = sorted({f"{s}USDT" for s in pair_matches})

                if not symbols and not is_bot_alert:
                    return

                post = {
                    "source": "telegram",
                    "source_id": f"tg_{event.message.id}_{event.chat_id}",
                    "channel": channel_name,
                    "author": None,
                    "title": None,
                    "content": text[:config.MAX_CONTENT_LENGTH],
                    "post_ts": event.message.date.replace(tzinfo=timezone.utc)
                              if event.message.date.tzinfo is None
                              else event.message.date,
                    "engagement_score": event.message.views or 0,
                    "symbols": symbols,
                    "batch_id": f"telegram_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                }
                batch_insert_posts([post])
                logger.debug("Telegram: ingested message from %s with symbols %s",
                             channel_name, symbols)
            except Exception as e:
                logger.error("Telegram handler error: %s", e)

        logger.info("Telegram: listening on %d channels", len(channel_entities))
        await self.client.run_until_disconnected()

    def collect_cycle(self) -> int:
        """No-op for Telegram — it's event-driven, not polled."""
        return 0
