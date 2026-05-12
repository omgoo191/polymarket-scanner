"""
src/services/notifier_service.py
Читает scored-trades из Kafka, шлёт алерты в Telegram.
"""
from __future__ import annotations

import logging

from config import load_config
from src.db import session as db_session
from src.db import repository as repo
from src.kafka.consumer import RadarConsumer
from src.notifications.telegram import TelegramNotifier

logger = logging.getLogger("radar.notifier")


class NotifierService:

    def __init__(self, consumer: RadarConsumer):
        self.consumer = consumer
        self.notifier = TelegramNotifier()

        cfg = load_config()
        self.rate_limit_min = cfg.get("alerts", {}).get("rate_limit_minutes", 30)

    async def run(self):
        logger.info("[Notifier] Starting...")
        async for message in self.consumer.messages():
            try:
                await self._process(message)
            except Exception as e:
                logger.error(f"[Notifier] Error processing message: {e}", exc_info=True)

    async def _process(self, msg: dict):
        market_id = msg["market_id"]
        trader = msg["trader"]

        # Rate limit check
        async with db_session.get_session() as session:
            recently_alerted = await repo.was_recently_alerted(
                session, market_id, trader, self.rate_limit_min
            )
        if recently_alerted:
            logger.debug(f"[Notifier] Rate limited: {trader[:8]} on {market_id[:8]}")
            return

        # Send alert
        sent = await self.notifier.send_alert(
            msg["short_msg"],
            msg["long_msg"],
        )

        if sent:
            async with db_session.get_session() as session:
                await repo.save_alert(session, {
                    "market_id": market_id,
                    "trader": trader,
                    "score": msg["score"],
                    "severity": msg["severity"],
                    "reasons": msg["reasons"],
                    "trade_ids": [msg["trade_id"]],
                })
            logger.info(
                f"[Notifier] Alert sent — {msg['severity']} "
                f"score={msg['score']} trader={trader[:10]}"
            )