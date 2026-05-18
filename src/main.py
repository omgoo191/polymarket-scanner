"""
src/main.py — Pipeline orchestrator
Modes: all | collector | scorer | notifier
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


async def _preflight():
    notifier = TelegramNotifier()
    if not await notifier.test_connection():
        logger.error("Telegram connection failed.")
        sys.exit(1)
    await db_session.create_all_tables()
    logger.info("Database ready.")
    return notifier


async def run_collector():
    logger.info("=== Collector starting ===")
    await _preflight()
    await notifier.send_startup_message()
    start_metrics_server(port=8000)

    producer = RadarProducer()
    await producer.start()

    try:
        await CollectorService(producer).run()
    finally:
        await producer.stop()
        await db_session.dispose()


async def run_scorer():
    logger.info("=== Scorer starting ===")
    await db_session.create_all_tables()

    consumer = RadarConsumer(topic="raw-trades", group_id="scorer-group")
    producer = RadarProducer()
    await consumer.start()
    await producer.start()

    try:
        await ScorerService(consumer, producer).run()
    finally:
        await consumer.stop()
        await producer.stop()
        await db_session.dispose()


async def run_notifier():
    logger.info("=== Notifier starting ===")
    await db_session.create_all_tables()

    notifier = TelegramNotifier()
    await notifier.test_connection()

    consumer = RadarConsumer(topic="scored-trades", group_id="notifier-group")
    await consumer.start()

    try:
        await NotifierService(consumer).run()
    finally:
        await consumer.stop()
        await db_session.dispose()


async def run_all():
    logger.info("=== Polymarket Smart Money Radar starting ===")
    notifier = await _preflight()
    await notifier.send_startup_message()
    start_metrics_server(port=8000)

    producer = RadarProducer()
    await producer.start()

    raw_consumer = RadarConsumer(topic="raw-trades", group_id="scorer-group")
    scored_consumer = RadarConsumer(topic="scored-trades", group_id="notifier-group")
    await raw_consumer.start()
    await scored_consumer.start()

    try:
        await asyncio.gather(
            CollectorService(producer).run(),
            ScorerService(raw_consumer, producer).run(),
            NotifierService(scored_consumer).run(),
        )
    finally:
        await producer.stop()
        await raw_consumer.stop()
        await scored_consumer.stop()
        await db_session.dispose()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    logger.info(f"Starting in mode: {mode}")

    modes = {
        "collector": run_collector,
        "scorer": run_scorer,
        "notifier": run_notifier,
        "all": run_all,
    }

    if mode not in modes:
        logger.error(f"Unknown mode: {mode}. Use: collector | scorer | notifier | all")
        sys.exit(1)

    try:
        asyncio.run(modes[mode]())
    except KeyboardInterrupt:
        logger.info("Radar stopped.")


if __name__ == "__main__":
    main()