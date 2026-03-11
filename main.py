"""
src/main.py — Pipeline orchestrator

Runtime flow:
  1. Market discovery  — fetch + tag insider-risk markets
  2. Trade polling     — fetch new trades, write to DB
  3. Funding enrichment — for new traders, fetch USDC funding history
  4. Scoring           — compute signal score per trade
  5. Summarization     — build short + long alert text
  6. Telegram notify   — send if score ≥ threshold and not rate-limited
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from config import load_config
from src.adapters.polymarket import PolymarketAdapter
from src.adapters.polygonscan import PolygonscanAdapter
from src.core.scorer import Scorer, SEVERITY_NONE, SEVERITY_LOW
from src.core.summarizer import Summarizer
from src.db import session as db_session
from src.db import repository as repo
from src.notifications.telegram import TelegramNotifier

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


# ── Pipeline class ────────────────────────────────────────────────────────────

class SmartMoneyRadar:

    def __init__(self):
        cfg = load_config()
        self.cfg = cfg
        self.polymarket = PolymarketAdapter()
        self.polygonscan = PolygonscanAdapter()
        self.scorer = Scorer()
        self.summarizer = Summarizer()
        self.notifier = TelegramNotifier()

        polling = cfg.get("polling", {})
        self.interval = polling.get("interval_seconds", 60)
        self.trades_lookback = polling.get("trades_lookback_minutes", 10)

        funding_cfg = cfg.get("funding", {})
        self.funding_lookback = funding_cfg.get("lookback_minutes", 180)

        alerts_cfg = cfg.get("alerts", {})
        self.rate_limit_min = alerts_cfg.get("rate_limit_minutes", 30)
        self.min_trade_size = alerts_cfg.get("min_trade_size_usd", 5000)

        # Market refresh every N poll cycles
        self._market_refresh_every = 10
        self._cycle = 0
        self._markets_cache: dict = {}   # market_id → Market

    # ─────────────────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("=== Polymarket Smart Money Radar starting ===")

        # Preflight checks
        if not await self.notifier.test_connection():
            logger.error("Telegram connection failed. Check bot_token and chat_id.")
            return

        await db_session.create_all_tables()
        logger.info("Database ready.")

        await self.notifier.send_startup_message()

        try:
            while True:
                try:
                    await self._run_cycle()
                except Exception as e:
                    logger.error(f"Cycle error: {e}", exc_info=True)
                    await self.notifier.send_error_alert(str(e))

                logger.info(f"Sleeping {self.interval}s until next cycle…")
                await asyncio.sleep(self.interval)
        finally:
            await self.polymarket.close()
            await self.polygonscan.close()
            await db_session.dispose()

    # ─────────────────────────────────────────────────────────────────────────

    async def _run_cycle(self):
        self._cycle += 1
        logger.info(f"── Cycle {self._cycle} ──────────────────────────────")

        # 1. Market discovery (every N cycles)
        if self._cycle == 1 or self._cycle % self._market_refresh_every == 0:
            await self._refresh_markets()

        insider_markets = list(self._markets_cache.values())
        if not insider_markets:
            logger.info("No insider-risk markets found yet.")
            return

        logger.info(f"Monitoring {len(insider_markets)} insider-risk markets")

        # 2. Trade polling
        since = datetime.now(tz=timezone.utc) - timedelta(minutes=self.trades_lookback)
        new_trades = await self._poll_trades(insider_markets, since)

        if not new_trades:
            logger.info("No new trades this cycle.")
            return

        logger.info(f"Processing {len(new_trades)} new trades")

        # 3–6. Enrich + score + alert for each trade
        await self._process_trades(new_trades, insider_markets)

    # ─────────────────────────────────────────────────────────────────────────

    async def _refresh_markets(self):
        logger.info("Refreshing market list…")
        markets = await self.polymarket.fetch_markets()

        async with db_session.get_session() as session:
            for m in markets:
                await repo.upsert_market(session, {
                    "id": m["id"],
                    "title": m["title"],
                    "slug": m.get("slug", ""),
                    "end_time": m.get("end_time"),
                    "is_active": m.get("is_active", True),
                    "insider_risk": m.get("insider_risk", False),
                })

            insider_markets = await repo.get_insider_risk_markets(session)
            self._markets_cache = {m.id: m for m in insider_markets}

        logger.info(f"Market cache: {len(self._markets_cache)} insider-risk markets")

    # ─────────────────────────────────────────────────────────────────────────

    async def _poll_trades(self, markets, since: datetime) -> list[dict]:
        """Fetch and store trades for all insider markets. Return new ones."""
        new_trade_records = []

        for market in markets:
            trades_raw = await self.polymarket.fetch_trades(market.id, since=since)

            async with db_session.get_session() as session:
                # Save price snapshot if available
                snapshots = await self.polymarket.fetch_price_snapshot(market.id)
                for snap in snapshots:
                    await repo.save_price_snapshot(
                        session, snap["market_id"], snap["outcome"], snap["price"]
                    )

                for trade in trades_raw:
                    if float(trade.get("size_usd", 0)) < self.min_trade_size:
                        continue

                    new_id = await repo.insert_trade_if_new(session, {
                        "tx_hash": trade["tx_hash"],
                        "market_id": trade["market_id"],
                        "trader": trade["trader"],
                        "outcome": trade.get("outcome", "YES"),
                        "side": trade.get("side", "BUY"),
                        "size_usd": trade["size_usd"],
                        "price": trade["price"],
                        "timestamp": trade["timestamp"],
                        "raw": trade.get("raw"),
                    })

                    if new_id is not None:
                        trade["_db_id"] = new_id
                        trade["_market"] = market
                        new_trade_records.append(trade)

        return new_trade_records

    # ─────────────────────────────────────────────────────────────────────────

    async def _process_trades(self, trades: list[dict], markets):
        """Enrich, score, summarize, and alert for each new trade."""

        # Gather unique trader addresses for batch funding lookup
        traders = list({t["trader"] for t in trades})
        funding_events = await self.polygonscan.get_funding_for_wallets(
            traders, lookback_minutes=self.funding_lookback
        )

        # Store funding events
        async with db_session.get_session() as session:
            for fe in funding_events:
                await repo.insert_funding_if_new(session, fe)

        # Build funding index: trader → list of events
        funding_index: dict[str, list] = {}
        for fe in funding_events:
            funding_index.setdefault(fe["to_address"], []).append(fe)

        # Process each trade
        for trade in trades:
            await self._score_and_alert(trade, funding_index)

    # ─────────────────────────────────────────────────────────────────────────

    async def _score_and_alert(self, trade: dict, funding_index: dict):
        trader = trade["trader"]
        market = trade["_market"]
        trade_id = trade["_db_id"]

        # ── Wallet enrichment ─────────────────────────────────────────────────
        wallet_age_days = await self.polygonscan.get_wallet_age_days(trader)
        wallet_tx_count = await self.polygonscan.get_transaction_count(trader)

        async with db_session.get_session() as session:
            wallet_profile = await repo.get_wallet_profile(session, trader)
            wallet_trades = wallet_profile.total_trades if wallet_profile else 0
            wallet_volume = float(wallet_profile.total_volume or 0) if wallet_profile else 0

        # ── Funding enrichment ────────────────────────────────────────────────
        trade_ts = trade["timestamp"]
        recent_funding_usd = None
        funding_minutes_before = None
        co_funded_wallets = None

        trader_funding = funding_index.get(trader, [])
        if trader_funding:
            # Find the funding event closest before the trade
            best: Optional[dict] = None
            best_delta = float("inf")
            for fe in trader_funding:
                fe_ts = fe["timestamp"]
                if fe_ts.tzinfo is None:
                    fe_ts = fe_ts.replace(tzinfo=timezone.utc)
                if trade_ts.tzinfo is None:
                    trade_ts = trade_ts.replace(tzinfo=timezone.utc)

                delta_min = (trade_ts - fe_ts).total_seconds() / 60
                if 0 <= delta_min < best_delta:
                    best = fe
                    best_delta = delta_min

            if best:
                recent_funding_usd = float(best["amount_usd"])
                funding_minutes_before = best_delta

                # Cluster check: how many other wallets from the same funder?
                async with db_session.get_session() as session:
                    since_cluster = trade_ts - timedelta(hours=6)
                    co_funded = await repo.get_wallets_funded_by(
                        session, best["from_address"], since=since_cluster
                    )
                    # How many of those co-funded wallets also traded this market?
                    active_count = 0
                    for addr in co_funded:
                        if addr == trader:
                            continue
                        trades_check = await repo.get_trades_for_market(
                            session, market.id, since=since_cluster
                        )
                        if any(t.trader == addr for t in trades_check):
                            active_count += 1
                    if active_count > 0:
                        co_funded_wallets = active_count + 1  # +1 for current trader

        # ── Price impact enrichment ───────────────────────────────────────────
        price_before = None
        async with db_session.get_session() as session:
            price_before = await repo.get_price_before(
                session, market.id, trade.get("outcome", "YES"), trade_ts
            )

        # ── Score ─────────────────────────────────────────────────────────────
        signal = self.scorer.score_trade(
            trade_id=trade_id,
            tx_hash=trade["tx_hash"],
            market_id=market.id,
            market_title=market.title,
            market_end_time=market.end_time,
            trader=trader,
            size_usd=float(trade["size_usd"]),
            price=float(trade["price"]),
            trade_timestamp=trade_ts,
            outcome=trade.get("outcome", "YES"),
            wallet_age_days=wallet_age_days,
            wallet_total_trades=wallet_trades or wallet_tx_count,
            wallet_total_volume=wallet_volume,
            recent_funding_usd=recent_funding_usd,
            funding_minutes_before=funding_minutes_before,
            price_before=price_before,
            co_funded_wallets_active=co_funded_wallets,
        )

        # Skip if below threshold
        if signal.severity in (SEVERITY_NONE, SEVERITY_LOW):
            return

        # ── Rate limit check ──────────────────────────────────────────────────
        async with db_session.get_session() as session:
            recently_alerted = await repo.was_recently_alerted(
                session, market.id, trader, self.rate_limit_min
            )
        if recently_alerted:
            logger.debug(
                f"[RateLimit] Skipping {trader[:8]} on {market.id[:8]} "
                f"(alerted within last {self.rate_limit_min}m)"
            )
            return

        # ── Summarize + Notify ────────────────────────────────────────────────
        short_msg, long_msg = self.summarizer.format_pair(signal)

        logger.info(
            f"[ALERT] {signal.severity} score={signal.score} "
            f"trader={trader[:10]} market={market.title[:40]}"
        )

        sent = await self.notifier.send_alert(short_msg, long_msg)

        if sent:
            async with db_session.get_session() as session:
                await repo.save_alert(session, {
                    "market_id": market.id,
                    "trader": trader,
                    "score": signal.score,
                    "severity": signal.severity,
                    "reasons": signal.reasons,
                    "trade_ids": signal.trade_ids,
                })


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    radar = SmartMoneyRadar()
    try:
        asyncio.run(radar.run())
    except KeyboardInterrupt:
        logger.info("Radar stopped by user.")


if __name__ == "__main__":
    main()
