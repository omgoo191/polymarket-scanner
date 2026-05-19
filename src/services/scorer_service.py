"""
src/services/scorer_service.py
Читает raw-trades из Kafka, скорит, публикует в scored-trades.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from config import load_config
from src.core.scorer import Scorer, SEVERITY_NONE, SEVERITY_LOW
from src.core.summarizer import Summarizer
from src.db import session as db_session
from src.db import repository as repo
from src.adapters.polygonscan import PolygonscanAdapter
from src.kafka.consumer import RadarConsumer
from src.kafka.producer import RadarProducer

logger = logging.getLogger("radar.scorer")


class ScorerService:

    def __init__(self, consumer: RadarConsumer, producer: RadarProducer):
        self.consumer = consumer
        self.producer = producer
        self.scorer = Scorer()
        self.summarizer = Summarizer()
        self.polygonscan = PolygonscanAdapter()

        cfg = load_config()
        self.funding_lookback = cfg.get("funding", {}).get("lookback_minutes", 180)

    async def run(self):
        logger.info("[Scorer] Starting...")
        async for message in self.consumer.messages():
            try:
                await self._process(message)
            except Exception as e:
                logger.error(f"[Scorer] Error processing message: {e}", exc_info=True)

    async def _process(self, msg: dict):
        logger.info(f"[Scorer] Got message: {msg.get('tx_hash', '')[:10]}")
        trader = msg["trader"]
        trade_ts = datetime.fromisoformat(msg["timestamp"])
        if trade_ts.tzinfo is None:
            trade_ts = trade_ts.replace(tzinfo=timezone.utc)

        # Wallet enrichment
        wallet_age_days = await self.polygonscan.get_wallet_age_days(trader)
        wallet_tx_count = await self.polygonscan.get_transaction_count(trader)

        async with db_session.get_session() as session:
            wallet_profile = await repo.get_wallet_profile(session, trader)
            wallet_trades = wallet_profile.total_trades if wallet_profile else 0
            wallet_volume = float(wallet_profile.total_volume or 0) if wallet_profile else 0

        # Funding enrichment
        funding_events = await self.polygonscan.get_funding_for_wallets(
            [trader], lookback_minutes=self.funding_lookback
        )
        recent_funding_usd = None
        funding_minutes_before = None

        if funding_events:
            best = None
            best_delta = float("inf")
            for fe in funding_events:
                fe_ts = fe["timestamp"]
                if fe_ts.tzinfo is None:
                    fe_ts = fe_ts.replace(tzinfo=timezone.utc)
                delta_min = (trade_ts - fe_ts).total_seconds() / 60
                if 0 <= delta_min < best_delta:
                    best = fe
                    best_delta = delta_min
            if best:
                recent_funding_usd = float(best["amount_usd"])
                funding_minutes_before = best_delta
        wallet_tx_count = await self.polygonscan.get_transaction_count(trader)
        logger.info(f"[Scorer] wallet enrichment: age={wallet_age_days} txcount={wallet_tx_count} trader={trader[:10]}")
        # Price impact
        price_before = None
        async with db_session.get_session() as session:
            price_before = await repo.get_price_before(
                session, msg["market_id"], msg.get("outcome", "YES"), trade_ts
            )

        # Score
        market_end_time = None
        if msg.get("market_end_time"):
            try:
                market_end_time = datetime.fromisoformat(msg["market_end_time"])
            except Exception:
                pass

        signal = self.scorer.score_trade(
            trade_id=msg["trade_id"],
            tx_hash=msg["tx_hash"],
            market_id=msg["market_id"],
            market_title=msg["market_title"],
            market_end_time=market_end_time,
            trader=trader,
            size_usd=float(msg["size_usd"]),
            price=float(msg["price"]),
            trade_timestamp=trade_ts,
            outcome=msg.get("outcome", "YES"),
            wallet_age_days=wallet_age_days,
            wallet_total_trades=wallet_trades or wallet_tx_count,
            wallet_total_volume=wallet_volume,
            recent_funding_usd=recent_funding_usd,
            funding_minutes_before=funding_minutes_before,
            price_before=price_before,
        )
        logger.info(f"[Scorer] score={signal.score} severity={signal.severity} trader={trader[:10]}")
        logger.info(
            f"[Scorer] score={signal.score} severity={signal.severity} size=${msg['size_usd']:.0f} market={msg['market_title'][:40]}")
        if signal.severity in (SEVERITY_NONE, SEVERITY_LOW):
            return

        short_msg, long_msg = self.summarizer.format_pair(signal)

        await self.producer.publish("scored-trades", {
            "trade_id": signal.trade_ids[0],
            "market_id": signal.market_id,
            "market_title": signal.market_title,
            "trader": signal.trader,
            "score": signal.score,
            "severity": signal.severity,
            "reasons": signal.reasons,
            "short_msg": short_msg,
            "long_msg": long_msg,
        })

        logger.info(
            f"[Scorer] {signal.severity} score={signal.score} "
            f"trader={trader[:10]} market={signal.market_title[:40]}"
        )