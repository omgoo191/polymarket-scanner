"""
tests/test_scorer.py
Unit tests for the signal scoring engine.
Run with: python -m pytest tests/
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Patch config loading so tests work without a real config.yaml
MOCK_CONFIG = {
    "telegram": {"bot_token": "x", "chat_id": "1"},
    "polygonscan": {"api_key": "x", "usdc_contract": "0x0"},
    "database": {"url": "postgresql+asyncpg://x:x@localhost/x"},
    "polymarket": {"rest_base_url": "https://x", "clob_base_url": "https://x", "markets_limit": 10},
    "polling": {"interval_seconds": 60, "trades_lookback_minutes": 10},
    "funding": {"lookback_minutes": 180},
    "scoring": {
        "strong_threshold": 70,
        "medium_threshold": 55,
        "weights": {
            "size": 30, "timing": 20, "wallet_history": 15,
            "funding": 15, "impact": 10, "cluster": 10
        }
    },
    "alerts": {"rate_limit_minutes": 30, "min_trade_size_usd": 5000},
    "insider_keywords": ["election", "merger"],
}

with patch("config.load_config", return_value=MOCK_CONFIG):
    from src.core.scorer import Scorer, SEVERITY_STRONG, SEVERITY_MEDIUM, SEVERITY_LOW, SEVERITY_NONE


def make_scorer():
    with patch("config.load_config", return_value=MOCK_CONFIG):
        return Scorer()


def base_trade(**overrides):
    now = datetime.now(tz=timezone.utc)
    defaults = dict(
        trade_id=1,
        tx_hash="0xabc",
        market_id="mkt1",
        market_title="Will candidate X win the election?",
        market_end_time=now + timedelta(hours=12),
        trader="0xtrader",
        size_usd=10_000,
        price=0.65,
        trade_timestamp=now,
        outcome="YES",
    )
    defaults.update(overrides)
    return defaults


class TestSizeScore:
    def test_small_trade_scores_low(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(size_usd=3_000))
        assert signal.triggered_features.get("size") is None

    def test_medium_trade_scores(self):
        scorer = make_scorer()
        # No timing/wallet/funding enrichment, so only size fires
        signal = scorer.score_trade(**base_trade(
            size_usd=25_000,
            market_end_time=None,  # disable timing
        ))
        size_pts, _ = signal.triggered_features.get("size", (0, ""))
        assert size_pts > 0

    def test_large_trade_max_size_score(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(
            size_usd=150_000,
            market_end_time=None,
        ))
        size_pts, note = signal.triggered_features["size"]
        assert size_pts == 30
        assert "150,000" in note or "Very large" in note


class TestTimingScore:
    def test_within_6h_max_timing(self):
        scorer = make_scorer()
        now = datetime.now(tz=timezone.utc)
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,  # below size threshold
            market_end_time=now + timedelta(hours=3),
        ))
        timing_pts, note = signal.triggered_features.get("timing", (0, ""))
        assert timing_pts == 20
        assert "3.0h" in note or "3h" in note

    def test_7_days_out_no_timing(self):
        scorer = make_scorer()
        now = datetime.now(tz=timezone.utc)
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,
            market_end_time=now + timedelta(days=7),
        ))
        assert "timing" not in signal.triggered_features

    def test_no_end_time_no_timing(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(size_usd=3_000, market_end_time=None))
        assert "timing" not in signal.triggered_features


class TestWalletScore:
    def test_new_wallet_scores(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,
            market_end_time=None,
            wallet_age_days=3,
            wallet_total_trades=2,
        ))
        pts, note = signal.triggered_features.get("wallet_history", (0, ""))
        assert pts > 0
        assert "new" in note.lower() or "limited" in note.lower()

    def test_veteran_wallet_no_score(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,
            market_end_time=None,
            wallet_age_days=365,
            wallet_total_trades=500,
        ))
        assert "wallet_history" not in signal.triggered_features


class TestFundingScore:
    def test_funded_30min_before_max(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,
            market_end_time=None,
            recent_funding_usd=100_000,
            funding_minutes_before=20,
        ))
        pts, note = signal.triggered_features.get("funding", (0, ""))
        assert pts == 15
        assert "100,000" in note

    def test_funded_4h_before_less_score(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,
            market_end_time=None,
            recent_funding_usd=50_000,
            funding_minutes_before=240,
        ))
        assert "funding" not in signal.triggered_features  # > 180 min window

    def test_no_funding_no_score(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(size_usd=3_000, market_end_time=None))
        assert "funding" not in signal.triggered_features


class TestSeverity:
    def test_strong_signal(self):
        scorer = make_scorer()
        now = datetime.now(tz=timezone.utc)
        signal = scorer.score_trade(**base_trade(
            size_usd=120_000,
            market_end_time=now + timedelta(hours=2),
            wallet_age_days=1,
            wallet_total_trades=1,
            recent_funding_usd=200_000,
            funding_minutes_before=15,
        ))
        assert signal.severity == SEVERITY_STRONG
        assert signal.score >= 70

    def test_medium_signal(self):
        scorer = make_scorer()
        now = datetime.now(tz=timezone.utc)
        signal = scorer.score_trade(**base_trade(
            size_usd=30_000,
            market_end_time=now + timedelta(hours=18),
            wallet_age_days=3,
        ))
        # Should be medium or strong depending on combo
        assert signal.severity in (SEVERITY_MEDIUM, SEVERITY_STRONG)

    def test_no_signal_small_old_wallet(self):
        scorer = make_scorer()
        signal = scorer.score_trade(**base_trade(
            size_usd=3_000,
            market_end_time=None,
            wallet_age_days=400,
            wallet_total_trades=1000,
        ))
        assert signal.severity == SEVERITY_NONE


class TestSummarizer:
    def test_reasons_built(self):
        from src.core.summarizer import Summarizer
        scorer = make_scorer()
        summarizer = Summarizer()
        now = datetime.now(tz=timezone.utc)
        signal = scorer.score_trade(**base_trade(
            size_usd=80_000,
            market_end_time=now + timedelta(hours=5),
            wallet_age_days=2,
            recent_funding_usd=150_000,
            funding_minutes_before=45,
        ))
        short, long = summarizer.format_pair(signal)
        assert len(signal.reasons) > 0
        assert "Score" in short
        assert "Trader" in long
        assert "Tx" in long


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
