"""
Discord Collector — Forward-only message ingestion from crypto servers.

Connects as a user account (selfbot) via discord.py-self, listens to all
servers the user has joined, writes raw messages to social_posts with
source='discord'. No parsing beyond basic symbol extraction via SymbolMatcher.

Feature-flagged: DISCORD_ENABLED=false by default.

Does NOT feed D2 cluster detector — Layer 1 only (Brain 1/Brain 2).
"""

import json
import logging
import os
import threading
import asyncio
from datetime import datetime, timezone

import config
from db import batch_insert_posts
from symbol_matcher import SymbolMatcher

logger = logging.getLogger("discord")

CHANNELS_FILE = os.path.join(os.path.dirname(__file__), '..', 'discord_channels.json')


def load_discord_config() -> dict:
    """Load Discord server/channel config from JSON."""
    path = os.path.abspath(CHANNELS_FILE)
    if not os.path.exists(path):
        logger.warning("discord_channels.json not found at %s", path)
        return {"servers": []}
    with open(path) as f:
        data = json.load(f)
    return data


class DiscordCollector:
    """
    Discord message collector using discord.py-self (user account).

    Listens to all servers the user account has joined via Gateway WebSocket.
    Messages arrive as events — no polling needed. Writes to social_posts
    with ON CONFLICT DO NOTHING for idempotent restarts.
    """

    def __init__(self, matcher: SymbolMatcher):
        self.matcher = matcher
        self.discord_config = load_discord_config()
        self._thread = None
        self._running = False
        self._monitored_channel_ids = set()

        # Build set of channel IDs to monitor (empty = listen to all)
        for server in self.discord_config.get("servers", []):
            for ch in server.get("channels", []):
                if ch.get("monitor", True):
                    ch_id = ch.get("id")
                    if ch_id:
                        self._monitored_channel_ids.add(int(ch_id))

    def _get_token(self) -> str | None:
        """Return the best available Discord token. Prefer user token for selfbot."""
        return config.DISCORD_USER_TOKEN or config.DISCORD_BOT_TOKEN or None

    def start(self):
        """Start the Discord listener if configured."""
        if not config.DISCORD_ENABLED:
            logger.info("Discord collector disabled (DISCORD_ENABLED=false)")
            return

        token = self._get_token()
        if not token:
            logger.info("Discord collector disabled — no DISCORD_USER_TOKEN or DISCORD_BOT_TOKEN in env")
            return

        is_user = bool(config.DISCORD_USER_TOKEN)
        mode = "user account (selfbot)" if is_user else "bot"
        if not self._monitored_channel_ids:
            logger.info("Discord collector: no channels configured, will listen to all "
                        "channels accessible to the %s", mode)

        logger.info("Discord: starting %s listener (monitoring %d specific channels)",
                     mode, len(self._monitored_channel_ids))
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                        name="discord-collector")
        self._thread.start()

    def _run_loop(self):
        """Run discord.py client in its own asyncio event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._listen())
        except Exception as e:
            logger.error("Discord listener error: %s", e, exc_info=True)
        finally:
            loop.close()

    async def _listen(self):
        """Connect to Discord and listen for messages."""
        try:
            import discord
        except ImportError:
            logger.error("discord.py-self not installed. Add 'discord.py-self' to requirements.txt")
            return

        is_user = bool(config.DISCORD_USER_TOKEN)
        token = self._get_token()

        # discord.py-self handles user tokens natively with the same Client API
        client = discord.Client()

        @client.event
        async def on_ready():
            guilds = [g.name for g in client.guilds]
            logger.info("Discord: connected as %s in %d servers: %s",
                        client.user, len(guilds), ", ".join(guilds))

        @client.event
        async def on_message(message):
            # Skip own messages
            if message.author.id == client.user.id:
                return

            # Skip if not in monitored channels (if channels are configured)
            if self._monitored_channel_ids and message.channel.id not in self._monitored_channel_ids:
                return

            text = message.content or ""
            if not text:
                return

            # Extract symbols
            symbols = self.matcher.extract_symbols(text)

            # Build server/channel path
            server_name = message.guild.name if message.guild else "DM"
            channel_name = message.channel.name if hasattr(message.channel, 'name') else "unknown"

            post = {
                "source": "discord",
                "source_id": f"discord_{message.id}",
                "channel": f"{server_name}/{channel_name}",
                "author": str(message.author),
                "title": None,
                "content": text[:config.MAX_CONTENT_LENGTH],
                "post_ts": message.created_at.replace(tzinfo=timezone.utc)
                          if message.created_at.tzinfo is None
                          else message.created_at,
                "engagement_score": 0,
                "symbols": symbols if symbols else [],
                "batch_id": f"discord_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            }

            try:
                batch_insert_posts([post])
                if symbols:
                    logger.debug("Discord: ingested from %s/%s with symbols %s",
                                 server_name, channel_name, symbols)
            except Exception as e:
                logger.error("Discord insert error: %s", e)

        @client.event
        async def on_disconnect():
            logger.warning("Discord: disconnected, will auto-reconnect")

        await client.start(token)

    def collect_cycle(self) -> int:
        """No-op for Discord — it's event-driven, not polled."""
        return 0
