"""
src/kafka/consumer.py — Kafka consumer wrapper
"""
from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from aiokafka import AIOKafkaConsumer

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092"


class RadarConsumer:

    def __init__(self, topic: str, group_id: str):
        self.topic = topic
        self.group_id = group_id
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=self.group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        logger.info(f"[Kafka] Consumer started — topic={self.topic} group={self.group_id}")

    async def stop(self):
        if self._consumer:
            await self._consumer.stop()
            logger.info(f"[Kafka] Consumer stopped — topic={self.topic}")

    async def messages(self) -> AsyncGenerator[dict, None]:
        if not self._consumer:
            raise RuntimeError("Consumer not started")
        async for msg in self._consumer:
            yield msg.value