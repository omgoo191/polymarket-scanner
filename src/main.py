"""
src/main.py — Pipeline orchestrator (Kafka-based)
Запускает три сервиса параллельно:
  CollectorService  — собирает трейды → raw-trades
  ScorerService     — raw-trades → scored-trades
  NotifierService   — scored-trades → Telegram
"""
from __future__ import annotations

import asyncio
import logging
import sys

import structlog

from config import load_config
from src.core.metrics import start_metrics_server
from src.db import session as db_session
from src.kafka.producer import RadarProducer
from src.kafka.consumer import RadarConsumer
from src.notifications.telegram import TelegramNotifier
from src.services.collector import CollectorService
from src.services.scorer_service import ScorerService
from src.services.notifier_service import NotifierService

# ── Logging setup ─────────────────────────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("radar.main")


async def run():
    logger.info("=== Polymarket Smart Money Radar (Kafka) starting ===")

    # Preflight
    notifier = TelegramNotifier()
    if not await notifier.test_connection():
        logger.error("Telegram connection failed.")
        return

    await db_session.create_all_tables()
    logger.info("Database ready.")
    await notifier.send_startup_message()

    start_metrics_server(port=8000)
    logger.info("Metrics server started on :8000")

    # Kafka
    producer = RadarProducer()
    await producer.start()

    raw_consumer = RadarConsumer(topic="raw-trades", group_id="scorer-group")
    scored_consumer = RadarConsumer(topic="scored-trades", group_id="notifier-group")
    await raw_consumer.start()
    await scored_consumer.start()

    # Services
    collector = CollectorService(producer)
    scorer = ScorerService(raw_consumer, producer)
    notifier_svc = NotifierService(scored_consumer)

    try:
        await asyncio.gather(
            collector.run(),
            scorer.run(),
            notifier_svc.run(),
        )
    finally:
        await producer.stop()
        await raw_consumer.stop()
        await scored_consumer.stop()
        await db_session.dispose()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Radar stopped.")


if __name__ == "__main__":
    main()