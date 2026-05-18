"""
src/core/scorer.py
Computes Signal Score (0–100) with category-aware thresholds.

Categories: politics, sports, crypto (default: politics)
Each category has different size thresholds and score thresholds.
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
    severity: str
    triggered_features: dict
    trade_ids: list[int]
    trade_size_usd: float
    trade_price: float
    trade_timestamp: datetime
    tx_hash: str
    outcome: str
    category: str = "politics"
    reasons: list[str] = field(default_factory=list)


SEVERITY_NONE   = "NONE"
SEVERITY_LOW    = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_STRONG = "STRONG"


class Scorer:

    def __init__(self):
        cfg = load_config()
        self.categories = cfg.get("categories", {})

        # Fallback defaults if no categories in config
        self._default_size = {
            "small": 1000, "medium": 3000,
            "large": 10000, "very_large": 30000
        }
        self._default_thresholds = {"medium": 35, "strong": 50}

        # Feature weights stay fixed
        weights = cfg.get("scoring", {}).get("weights", {})
        self.w_size    = weights.get("size", 30)
        self.w_timing  = weights.get("timing", 20)
        self.w_wallet  = weights.get("wallet_history", 15)
        self.w_funding = weights.get("funding", 15)
        self.w_impact  = weights.get("impact", 10)
        self.w_cluster = weights.get("cluster", 10)

    def detect_category(self, market_title: str) -> str:
        title_lower = market_title.lower()
        for category, cfg in self.categories.items():
            keywords = [kw.lower() for kw in cfg.get("keywords", [])]
            if any(kw in title_lower for kw in keywords):
                return category
        return "politics"  # default

    def _get_size_thresholds(self, category: str) -> dict:
        return self.categories.get(category, {}).get(
            "size_thresholds", self._default_size
        )

    def _get_score_thresholds(self, category: str) -> dict:
        return self.categories.get(category, {}).get(
            "score_thresholds", self._default_thresholds
        )

    def score_trade(
        self,
        *,
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
        wallet_age_days: Optional[float] = None,
        wallet_total_trades: Optional[int] = None,
        wallet_total_volume: Optional[float] = None,
        recent_funding_usd: Optional[float] = None,
        funding_minutes_before: Optional[float] = None,
        price_before: Optional[float] = None,
        co_funded_wallets_active: Optional[int] = None,
    ) -> ScoredSignal:
        category = self.detect_category(market_title)
        size_thresholds = self._get_size_thresholds(category)
        score_thresholds = self._get_score_thresholds(category)

        features = {}
        total = 0

        # ── 1. SizeScore (0–30) ──────────────────────────────────────────────
        size_pts, size_note = self._size_score(size_usd, size_thresholds)
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
        impact_pts, impact_note = self._impact_score(
            size_usd, price, price_before, size_thresholds
        )
        if impact_pts > 0:
            features["impact"] = (impact_pts, impact_note)
            total += impact_pts

        # ── 6. ClusterScore (0–10) ───────────────────────────────────────────
        cluster_pts, cluster_note = self._cluster_score(co_funded_wallets_active)
        if cluster_pts > 0:
            features["cluster"] = (cluster_pts, cluster_note)
            total += cluster_pts

        score = min(total, 100)
        severity = self._severity(score, score_thresholds)

        logger.debug(
            f"[Scorer] {trader[:8]} | {market_title[:40]} | "
            f"category={category} score={score} severity={severity}"
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
            category=category,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Feature scorers
    # ─────────────────────────────────────────────────────────────────────────

    def _size_score(self, size_usd: float, thresholds: dict) -> tuple[int, str]:
        very_large = thresholds.get("very_large", 30000)
        large      = thresholds.get("large", 10000)
        medium     = thresholds.get("medium", 3000)
        small      = thresholds.get("small", 1000)

        if size_usd >= very_large:
            return self.w_size, f"Very large entry: ~${size_usd:,.0f}"
        elif size_usd >= large:
            pts = int(self.w_size * 0.80)
            return pts, f"Large entry: ~${size_usd:,.0f}"
        elif size_usd >= medium:
            pts = int(self.w_size * 0.50)
            return pts, f"Notable entry: ~${size_usd:,.0f}"
        elif size_usd >= small:
            pts = int(self.w_size * 0.25)
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
            return 0, ""
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
        thresholds: dict,
    ) -> tuple[int, str]:
        large = thresholds.get("large", 10000)
        if price_before is None or size_usd < large:
            return 0, ""

        impact_pct = abs(price_after - price_before) / max(price_before, 0.01) * 100
        very_large = thresholds.get("very_large", 30000)

        if impact_pct < 0.5 and size_usd >= very_large:
            return self.w_impact, (
                f"High size (${size_usd:,.0f}) with low price impact "
                f"({impact_pct:.1f}%) — possible accumulation"
            )
        elif impact_pct < 1.0 and size_usd >= large:
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

    def _severity(self, score: int, thresholds: dict) -> str:
        strong = thresholds.get("strong", 50)
        medium = thresholds.get("medium", 35)
        if score >= strong:
            return SEVERITY_STRONG
        elif score >= medium:
            return SEVERITY_MEDIUM
        elif score > 0:
            return SEVERITY_LOW
        return SEVERITY_NONE