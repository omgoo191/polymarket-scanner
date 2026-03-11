"""
src/core/scorer.py
Computes the Signal Score (0–100) from observed features.

Score breakdown:
  SizeScore         0–30  larger notional → higher
  TimingScore       0–20  closer to deadline → higher
  WalletHistoryScore 0–15 new / low activity wallet → higher
  FundingScore      0–15  recent funding before bet → higher
  ImpactScore       0–10  large trade, small price move → higher
  ClusterScore      0–10  same funder, multiple wallets → higher
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import load_config

logger = logging.getLogger(__name__)


@dataclass
class ScoredSignal:
    trader: str
    market_id: str
    market_title: str
    score: int
    severity: str                    # STRONG / MEDIUM / LOW / NONE
    triggered_features: dict         # feature_name → (points, explanation)
    trade_ids: list[int]
    trade_size_usd: float
    trade_price: float
    trade_timestamp: datetime
    tx_hash: str
    outcome: str
    reasons: list[str] = field(default_factory=list)  # filled by summarizer


SEVERITY_NONE   = "NONE"
SEVERITY_LOW    = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_STRONG = "STRONG"


class Scorer:
    """
    Stateless scoring engine. Call score_trade() for each new trade.
    """

    def __init__(self):
        cfg = load_config()
        scoring = cfg.get("scoring", {})
        self.strong_threshold = scoring.get("strong_threshold", 70)
        self.medium_threshold = scoring.get("medium_threshold", 55)
        weights = scoring.get("weights", {})
        self.w_size    = weights.get("size", 30)
        self.w_timing  = weights.get("timing", 20)
        self.w_wallet  = weights.get("wallet_history", 15)
        self.w_funding = weights.get("funding", 15)
        self.w_impact  = weights.get("impact", 10)
        self.w_cluster = weights.get("cluster", 10)

    def score_trade(
        self,
        *,
        # Required
        trade_id: int,
        tx_hash: str,
        market_id: str,
        market_title: str,
        market_end_time: Optional[datetime],
        trader: str,
        size_usd: float,
        price: float,
        trade_timestamp: datetime,
        outcome: str,
        # Optional enrichment
        wallet_age_days: Optional[float] = None,
        wallet_total_trades: Optional[int] = None,
        wallet_total_volume: Optional[float] = None,
        recent_funding_usd: Optional[float] = None,
        funding_minutes_before: Optional[float] = None,
        price_before: Optional[float] = None,
        co_funded_wallets_active: Optional[int] = None,
    ) -> ScoredSignal:
        features = {}
        total = 0

        # ── 1. SizeScore (0–30) ──────────────────────────────────────────────
        size_pts, size_note = self._size_score(size_usd)
        if size_pts > 0:
            features["size"] = (size_pts, size_note)
            total += size_pts

        # ── 2. TimingScore (0–20) ────────────────────────────────────────────
        timing_pts, timing_note = self._timing_score(trade_timestamp, market_end_time)
        if timing_pts > 0:
            features["timing"] = (timing_pts, timing_note)
            total += timing_pts

        # ── 3. WalletHistoryScore (0–15) ─────────────────────────────────────
        wallet_pts, wallet_note = self._wallet_score(
            wallet_age_days, wallet_total_trades, wallet_total_volume
        )
        if wallet_pts > 0:
            features["wallet_history"] = (wallet_pts, wallet_note)
            total += wallet_pts

        # ── 4. FundingScore (0–15) ───────────────────────────────────────────
        funding_pts, funding_note = self._funding_score(
            recent_funding_usd, funding_minutes_before
        )
        if funding_pts > 0:
            features["funding"] = (funding_pts, funding_note)
            total += funding_pts

        # ── 5. ImpactScore (0–10) ────────────────────────────────────────────
        impact_pts, impact_note = self._impact_score(size_usd, price, price_before)
        if impact_pts > 0:
            features["impact"] = (impact_pts, impact_note)
            total += impact_pts

        # ── 6. ClusterScore (0–10) ───────────────────────────────────────────
        cluster_pts, cluster_note = self._cluster_score(co_funded_wallets_active)
        if cluster_pts > 0:
            features["cluster"] = (cluster_pts, cluster_note)
            total += cluster_pts

        score = min(total, 100)
        severity = self._severity(score)

        logger.debug(
            f"[Scorer] {trader[:8]} | {market_title[:40]} | "
            f"score={score} severity={severity} features={list(features.keys())}"
        )

        return ScoredSignal(
            trader=trader,
            market_id=market_id,
            market_title=market_title,
            score=score,
            severity=severity,
            triggered_features=features,
            trade_ids=[trade_id],
            trade_size_usd=size_usd,
            trade_price=price,
            trade_timestamp=trade_timestamp,
            tx_hash=tx_hash,
            outcome=outcome,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Feature scorers
    # ─────────────────────────────────────────────────────────────────────────

    def _size_score(self, size_usd: float) -> tuple[int, str]:
        """
        Tiered: larger bet → more points.
        $5k–$20k → 10, $20k–$50k → 18, $50k–$100k → 24, $100k+ → 30
        """
        if size_usd >= 100_000:
            return self.w_size, f"Very large entry: ~${size_usd:,.0f}"
        elif size_usd >= 50_000:
            pts = int(self.w_size * 0.80)
            return pts, f"Large entry: ~${size_usd:,.0f}"
        elif size_usd >= 20_000:
            pts = int(self.w_size * 0.60)
            return pts, f"Notable entry: ~${size_usd:,.0f}"
        elif size_usd >= 5_000:
            pts = int(self.w_size * 0.33)
            return pts, f"Entry: ~${size_usd:,.0f}"
        else:
            return 0, ""

    def _timing_score(
        self, trade_ts: datetime, end_time: Optional[datetime]
    ) -> tuple[int, str]:
        if not end_time:
            return 0, ""

        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        if trade_ts.tzinfo is None:
            trade_ts = trade_ts.replace(tzinfo=timezone.utc)

        hours_to_end = (end_time - trade_ts).total_seconds() / 3600

        if hours_to_end < 0:
            return 0, ""  # market already ended
        elif hours_to_end <= 6:
            return self.w_timing, f"Placed within ~{hours_to_end:.1f}h of deadline"
        elif hours_to_end <= 24:
            pts = int(self.w_timing * 0.75)
            return pts, f"Placed ~{hours_to_end:.0f}h before deadline"
        elif hours_to_end <= 72:
            pts = int(self.w_timing * 0.40)
            return pts, f"Placed ~{hours_to_end:.0f}h before market resolves"
        else:
            return 0, ""

    def _wallet_score(
        self,
        age_days: Optional[float],
        total_trades: Optional[int],
        total_volume: Optional[float],
    ) -> tuple[int, str]:
        pts = 0
        notes = []

        if age_days is not None and age_days < 7:
            pts += int(self.w_wallet * 0.60)
            notes.append(f"wallet < {age_days:.0f} days old")
        elif age_days is not None and age_days < 30:
            pts += int(self.w_wallet * 0.30)
            notes.append(f"wallet ~{age_days:.0f} days old")

        if total_trades is not None and total_trades < 10:
            pts += int(self.w_wallet * 0.40)
            notes.append(f"only {total_trades} prior trades")
        elif total_trades is not None and total_trades < 30:
            pts += int(self.w_wallet * 0.15)
            notes.append(f"{total_trades} prior trades")

        pts = min(pts, self.w_wallet)
        if pts > 0:
            note = "Wallet appears new / limited history" + (
                f" ({', '.join(notes)})" if notes else ""
            )
            return pts, note
        return 0, ""

    def _funding_score(
        self,
        amount_usd: Optional[float],
        minutes_before: Optional[float],
    ) -> tuple[int, str]:
        if not amount_usd or not minutes_before or minutes_before < 0:
            return 0, ""

        if minutes_before <= 30:
            pts = self.w_funding
            note = f"Wallet funded +${amount_usd:,.0f} USDC ~{minutes_before:.0f} min before trade"
        elif minutes_before <= 60:
            pts = int(self.w_funding * 0.85)
            note = f"Wallet funded +${amount_usd:,.0f} USDC ~{minutes_before:.0f} min before trade"
        elif minutes_before <= 180:
            pts = int(self.w_funding * 0.60)
            note = f"Wallet funded +${amount_usd:,.0f} USDC ~{minutes_before / 60:.1f}h before trade"
        else:
            return 0, ""

        return pts, note

    def _impact_score(
        self,
        size_usd: float,
        price_after: float,
        price_before: Optional[float],
    ) -> tuple[int, str]:
        """
        High size + low price impact → possible careful accumulation.
        Only fire if trade is large enough to matter.
        """
        if price_before is None or size_usd < 10_000:
            return 0, ""

        impact_pct = abs(price_after - price_before) / max(price_before, 0.01) * 100

        if impact_pct < 0.5 and size_usd >= 50_000:
            return self.w_impact, (
                f"High size (${size_usd:,.0f}) with low price impact "
                f"({impact_pct:.1f}%) — possible accumulation"
            )
        elif impact_pct < 1.0 and size_usd >= 20_000:
            pts = int(self.w_impact * 0.50)
            return pts, (
                f"${size_usd:,.0f} trade with minimal price impact ({impact_pct:.1f}%)"
            )
        return 0, ""

    def _cluster_score(self, co_funded_wallets: Optional[int]) -> tuple[int, str]:
        if not co_funded_wallets or co_funded_wallets < 2:
            return 0, ""
        pts = min(int(self.w_cluster * (co_funded_wallets - 1) / 3), self.w_cluster)
        return pts, (
            f"{co_funded_wallets} wallets funded by same source acting on this market"
        )

    def _severity(self, score: int) -> str:
        if score >= self.strong_threshold:
            return SEVERITY_STRONG
        elif score >= self.medium_threshold:
            return SEVERITY_MEDIUM
        elif score > 0:
            return SEVERITY_LOW
        return SEVERITY_NONE
