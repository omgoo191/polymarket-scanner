"""
src/db/models.py — SQLAlchemy ORM models
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, Index, Integer, JSON,
    Numeric, String, Text, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    id          = Column(Text, primary_key=True)
    title       = Column(Text, nullable=False)
    slug        = Column(Text)
    end_time    = Column(TIMESTAMPTZ)
    is_active   = Column(Boolean, default=True)
    insider_risk = Column(Boolean, default=False)
    created_at  = Column(TIMESTAMPTZ, default=datetime.utcnow)
    updated_at  = Column(TIMESTAMPTZ, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Market {self.id[:8]} {self.title[:40]}>"


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        UniqueConstraint("tx_hash", "trader", "market_id", name="uq_trade"),
    )

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    tx_hash     = Column(Text, nullable=False)
    market_id   = Column(Text, nullable=False)
    trader      = Column(Text, nullable=False)
    outcome     = Column(Text)
    side        = Column(Text)
    size_usd    = Column(Numeric(20, 4))
    price       = Column(Numeric(10, 6))
    timestamp   = Column(TIMESTAMPTZ, nullable=False)
    raw         = Column(JSONB)
    created_at  = Column(TIMESTAMPTZ, default=datetime.utcnow)

    def __repr__(self):
        return f"<Trade {self.tx_hash[:10]} {self.trader[:8]} ${self.size_usd}>"


class FundingEvent(Base):
    __tablename__ = "funding_events"

    id           = Column(BigInteger, primary_key=True, autoincrement=True)
    tx_hash      = Column(Text, unique=True, nullable=False)
    to_address   = Column(Text, nullable=False)
    from_address = Column(Text)
    amount_usd   = Column(Numeric(20, 4))
    timestamp    = Column(TIMESTAMPTZ, nullable=False)
    created_at   = Column(TIMESTAMPTZ, default=datetime.utcnow)

    def __repr__(self):
        return f"<Funding ${self.amount_usd} → {self.to_address[:8]}>"


class WalletProfile(Base):
    __tablename__ = "wallet_profiles"

    address      = Column(Text, primary_key=True)
    first_seen   = Column(TIMESTAMPTZ)
    last_seen    = Column(TIMESTAMPTZ)
    total_trades = Column(Integer, default=0)
    total_volume = Column(Numeric(20, 4), default=0)
    updated_at   = Column(TIMESTAMPTZ, default=datetime.utcnow, onupdate=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id  = Column(Text, nullable=False)
    trader     = Column(Text, nullable=False)
    score      = Column(Integer, nullable=False)
    severity   = Column(Text, nullable=False)
    reasons    = Column(JSONB)
    trade_ids  = Column(JSONB)
    sent_at    = Column(TIMESTAMPTZ, default=datetime.utcnow)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    market_id  = Column(Text, nullable=False)
    outcome    = Column(Text)
    price      = Column(Numeric(10, 6))
    timestamp  = Column(TIMESTAMPTZ, default=datetime.utcnow)
