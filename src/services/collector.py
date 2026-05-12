"""
src/services/collector.py
Собирает трейды с Polymarket и публикует в Kafka topic raw-trades.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from config import load_config
from src.adapters.polymarket import PolymarketAdapter
from src.db import session as db_session
from src.db import repository as repo
from src.kafka.producer import RadarProducer

logger = logging.getLogger("radar.collector")


class CollectorService:

    def __init__(self, producer: RadarProducer):
        self.producer = producer
        self.polymarket = PolymarketAdapter()

        cfg = load_config()
        polling = cfg.get("polling", {})
        self.interval = polling.get("interval_seconds", 60)
        self.trades_lookback = polling.get("trades_lookback_minutes", 10)
        self.min_trade_size = cfg.get("alerts", {}).get("min_trade_size_usd", 1000)

        self._market_refresh_every = 10
        self._cycle = 0
        self._markets_cache: dict = {}

    async def run(self):
        logger.info("[Collector] Starting...")
        while True:
            try:
                await self._run_cycle()
            except Exception as e:
                logger.error(f"[Collector] Cycle error: {e}", exc_info=True)
            await asyncio.sleep(self.interval)

    async def _run_cycle(self):
        self._cycle += 1

        if self._cycle == 1 or self._cycle % self._market_refresh_every == 0:
            await self._refresh_markets()

        if not self._markets_cache:
            return

        since = datetime.now(tz=timezone.utc) - timedelta(minutes=self.trades_lookback)
        all_trades = await self.polymarket.fetch_all_recent_trades(since)

        condition_map = {
            m.condition_id: m
            for m in self._markets_cache.values()
            if m.condition_id
        }

        new_count = 0
        async with db_session.get_session() as session:
            for trade in all_trades:
                condition_id = trade.get("conditionId", "")
                market = condition_map.get(condition_id)
                if not market:
                    continue
                if float(trade.get("size_usd", 0)) < self.min_trade_size:
                    continue

                new_id = await repo.insert_trade_if_new(session, {
                    "tx_hash": trade["tx_hash"],
                    "market_id": market.id,
                    "trader": trade["trader"],
                    "outcome": trade.get("outcome", "YES"),
                    "side": trade.get("side", "BUY"),
                    "size_usd": trade["size_usd"],
                    "price": trade["price"],
                    "timestamp": trade["timestamp"],
                    "raw": trade.get("raw"),
                })

                if new_id is not None:
                    await self.producer.publish("raw-trades", {
                        "trade_id": new_id,
                        "tx_hash": trade["tx_hash"],
                        "market_id": market.id,
                        "market_title": market.title,
                        "market_end_time": str(market.end_time) if market.end_time else None,
                        "trader": trade["trader"],
                        "size_usd": float(trade["size_usd"]),
                        "price": float(trade["price"]),
                        "timestamp": str(trade["timestamp"]),
                        "outcome": trade.get("outcome", "YES"),
                    })
                    new_count += 1

        if new_count:
            logger.info(f"[Collector] Published {new_count} new trades to Kafka")

    async def _refresh_markets(self):
        logger.info("[Collector] Refreshing markets...")
        markets = await self.polymarket.fetch_markets()
        async with db_session.get_session() as session:
            for m in markets:
                await repo.upsert_market(session, {
                    "id": m["id"],
                    "condition_id": m.get("condition_id", ""),
                    "title": m["title"],
                    "slug": m.get("slug", ""),
                    "end_time": m.get("end_time"),
                    "is_active": m.get("is_active", True),
                    "insider_risk": m.get("insider_risk", False),
                })
            insider_markets = await repo.get_insider_risk_markets(session)
            self._markets_cache = {m.id: m for m in insider_markets}
        logger.info(f"[Collector] {len(self._markets_cache)} insider-risk markets cached")