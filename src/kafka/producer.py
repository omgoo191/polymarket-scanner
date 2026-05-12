"""
src/kafka/producer.py — Kafka producer wrapper
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092"


class RadarProducer:

    def __init__(self):
        self._producer: AIOKafkaProducer | None = None

    async def start(self):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        )
        await self._producer.start()
        logger.info("[Kafka] Producer started")

    async def stop(self):
        if self._producer:
            await self._producer.stop()
            logger.info("[Kafka] Producer stopped")

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        if not self._producer:
            raise RuntimeError("Producer not started")
        await self._producer.send_and_wait(topic, message)
        logger.debug(f"[Kafka] Published to {topic}: {str(message)[:80]}")