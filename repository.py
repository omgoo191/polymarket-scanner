"""
src/db/repository.py — Data access layer (all DB queries live here)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func, and_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Alert, FundingEvent, Market, PriceSnapshot, Trade, WalletProfile
)


# ─────────────────────────────────────────────────────────────────────────────
# Markets
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_market(session: AsyncSession, market: dict) -> None:
    stmt = pg_insert(Market).values(**market)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "title": stmt.excluded.title,
            "is_active": stmt.excluded.is_active,
            "insider_risk": stmt.excluded.insider_risk,
            "end_time": stmt.excluded.end_time,
            "updated_at": datetime.utcnow(),
        }
    )
    await session.execute(stmt)


async def get_insider_risk_markets(session: AsyncSession) -> list[Market]:
    result = await session.execute(
        select(Market).where(
            and_(Market.insider_risk == True, Market.is_active == True)
        )
    )
    return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# Trades
# ─────────────────────────────────────────────────────────────────────────────

async def insert_trade_if_new(session: AsyncSession, trade: dict) -> Optional[int]:
    """Insert trade; return new row id or None if duplicate."""
    stmt = pg_insert(Trade).values(**trade)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_trade")
    stmt = stmt.returning(Trade.id)
    result = await session.execute(stmt)
    row = result.fetchone()
    return row[0] if row else None


async def get_trades_for_market(
    session: AsyncSession,
    market_id: str,
    since: Optional[datetime] = None,
) -> list[Trade]:
    q = select(Trade).where(Trade.market_id == market_id)
    if since:
        q = q.where(Trade.timestamp >= since)
    q = q.order_by(Trade.timestamp.desc())
    result = await session.execute(q)
    return result.scalars().all()


async def get_trader_history(
    session: AsyncSession,
    trader: str,
) -> list[Trade]:
    result = await session.execute(
        select(Trade)
        .where(Trade.trader == trader)
        .order_by(Trade.timestamp.asc())
    )
    return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# Funding events
# ─────────────────────────────────────────────────────────────────────────────

async def insert_funding_if_new(session: AsyncSession, event: dict) -> Optional[int]:
    stmt = pg_insert(FundingEvent).values(**event)
    stmt = stmt.on_conflict_do_nothing(index_elements=["tx_hash"])
    stmt = stmt.returning(FundingEvent.id)
    result = await session.execute(stmt)
    row = result.fetchone()
    return row[0] if row else None


async def get_funding_for_wallet(
    session: AsyncSession,
    address: str,
    since: datetime,
) -> list[FundingEvent]:
    result = await session.execute(
        select(FundingEvent)
        .where(
            and_(
                FundingEvent.to_address == address,
                FundingEvent.timestamp >= since,
            )
        )
        .order_by(FundingEvent.timestamp.desc())
    )
    return result.scalars().all()


async def get_wallets_funded_by(
    session: AsyncSession,
    from_address: str,
    since: datetime,
) -> list[str]:
    """Return list of addresses funded by a given source wallet."""
    result = await session.execute(
        select(FundingEvent.to_address)
        .where(
            and_(
                FundingEvent.from_address == from_address,
                FundingEvent.timestamp >= since,
            )
        )
        .distinct()
    )
    return [row[0] for row in result.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Wallet profiles
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_wallet_profile(session: AsyncSession, address: str, trade: Trade) -> None:
    stmt = pg_insert(WalletProfile).values(
        address=address,
        first_seen=trade.timestamp,
        last_seen=trade.timestamp,
        total_trades=1,
        total_volume=float(trade.size_usd or 0),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["address"],
        set_={
            "last_seen": trade.timestamp,
            "total_trades": WalletProfile.total_trades + 1,
            "total_volume": WalletProfile.total_volume + float(trade.size_usd or 0),
            "updated_at": datetime.utcnow(),
        }
    )
    await session.execute(stmt)


async def get_wallet_profile(
    session: AsyncSession,
    address: str,
) -> Optional[WalletProfile]:
    result = await session.execute(
        select(WalletProfile).where(WalletProfile.address == address)
    )
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Alerts
# ─────────────────────────────────────────────────────────────────────────────

async def was_recently_alerted(
    session: AsyncSession,
    market_id: str,
    trader: str,
    within_minutes: int,
) -> bool:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=within_minutes)
    result = await session.execute(
        select(func.count(Alert.id)).where(
            and_(
                Alert.market_id == market_id,
                Alert.trader == trader,
                Alert.sent_at >= cutoff,
            )
        )
    )
    count = result.scalar()
    return count > 0


async def save_alert(session: AsyncSession, alert: dict) -> int:
    obj = Alert(**alert)
    session.add(obj)
    await session.flush()
    return obj.id


# ─────────────────────────────────────────────────────────────────────────────
# Price snapshots
# ─────────────────────────────────────────────────────────────────────────────

async def save_price_snapshot(session: AsyncSession, market_id: str, outcome: str, price: float) -> None:
    obj = PriceSnapshot(market_id=market_id, outcome=outcome, price=price)
    session.add(obj)


async def get_price_before(
    session: AsyncSession,
    market_id: str,
    outcome: str,
    before: datetime,
    window_minutes: int = 60,
) -> Optional[float]:
    """Return the price closest to `before - window` for impact comparison."""
    since = before - timedelta(minutes=window_minutes)
    result = await session.execute(
        select(PriceSnapshot.price)
        .where(
            and_(
                PriceSnapshot.market_id == market_id,
                PriceSnapshot.outcome == outcome,
                PriceSnapshot.timestamp >= since,
                PriceSnapshot.timestamp <= before,
            )
        )
        .order_by(PriceSnapshot.timestamp.asc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return float(row) if row else None
