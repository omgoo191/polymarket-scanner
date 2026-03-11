"""
src/notifications/telegram.py
Sends short + long alert messages to a Telegram DM.
Uses python-telegram-bot (async).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import load_config

logger = logging.getLogger(__name__)


class TelegramNotifier:

    def __init__(self):
        cfg = load_config()
        self._token = cfg["telegram"]["bot_token"]
        self._chat_id = cfg["telegram"]["chat_id"]
        self._bot: Optional[Bot] = None

    def _get_bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(token=self._token)
        return self._bot

    async def send_alert(self, short_msg: str, long_msg: str) -> bool:
        """
        Send both short + long messages.
        Returns True if at least the short message was delivered.
        """
        bot = self._get_bot()
        short_ok = await self._send(bot, short_msg)
        long_ok = await self._send(bot, long_msg)
        return short_ok

    async def _send(self, bot: Bot, text: str) -> bool:
        try:
            await bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as e:
            logger.error(f"[Telegram] Failed to send message: {e}")
            # Try without markdown if parse error
            if "parse" in str(e).lower() or "entity" in str(e).lower():
                try:
                    await bot.send_message(
                        chat_id=self._chat_id,
                        text=text,
                        disable_web_page_preview=True,
                    )
                    return True
                except TelegramError as e2:
                    logger.error(f"[Telegram] Retry also failed: {e2}")
            return False

    async def send_startup_message(self) -> bool:
        """Send a startup notification so you know the bot is live."""
        msg = (
            "🟢 *Polymarket Smart Money Radar* is running\n\n"
            "Monitoring insider-risk markets.\n"
            "You'll receive alerts when suspicious activity is detected."
        )
        bot = self._get_bot()
        return await self._send(bot, msg)

    async def send_error_alert(self, error_msg: str) -> bool:
        """Send a simple error notification."""
        msg = f"🔴 *Radar error*\n`{error_msg[:200]}`"
        bot = self._get_bot()
        return await self._send(bot, msg)

    async def test_connection(self) -> bool:
        """Verify Telegram bot credentials work."""
        try:
            bot = self._get_bot()
            me = await bot.get_me()
            logger.info(f"[Telegram] Connected as @{me.username}")
            return True
        except TelegramError as e:
            logger.error(f"[Telegram] Connection test failed: {e}")
            return False
