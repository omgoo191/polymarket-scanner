"""
src/core/summarizer.py
Translates a ScoredSignal into short + long human-readable alert messages.
"""
from __future__ import annotations

from datetime import timezone

from src.core.scorer import ScoredSignal, SEVERITY_STRONG, SEVERITY_MEDIUM


SEVERITY_EMOJI = {
    SEVERITY_STRONG: "🚨",
    SEVERITY_MEDIUM: "⚠️",
}

OUTCOME_EMOJI = {
    "YES": "✅",
    "NO": "❌",
}


class Summarizer:

    def build_reasons(self, signal: ScoredSignal) -> list[str]:
        """
        Extract human-readable bullet reasons from triggered features.
        Ordered by point value (most significant first).
        """
        reasons = []
        sorted_features = sorted(
            signal.triggered_features.items(),
            key=lambda x: x[1][0],
            reverse=True,
        )
        for _feature_name, (pts, explanation) in sorted_features:
            if explanation:
                reasons.append(explanation)
        return reasons

    def short_message(self, signal: ScoredSignal, reasons: list[str]) -> str:
        """
        Compact alert for quick scanning — under ~300 chars.
        """
        emoji = SEVERITY_EMOJI.get(signal.severity, "🔍")
        outcome_emoji = OUTCOME_EMOJI.get(signal.outcome.upper(), "")

        lines = [
            f"{emoji} *{signal.severity} SIGNAL* [Score: {signal.score}/100]",
            f"📊 {signal.market_title}",
            "",
        ]

        for r in reasons[:5]:
            lines.append(f"• {r}")

        lines += [
            "",
            f"Outcome: {outcome_emoji} {signal.outcome} @ {signal.trade_price:.3f}",
        ]

        return "\n".join(lines)

    def long_message(self, signal: ScoredSignal, reasons: list[str]) -> str:
        """
        Detailed alert with raw context for investigation.
        """
        emoji = SEVERITY_EMOJI.get(signal.severity, "🔍")
        outcome_emoji = OUTCOME_EMOJI.get(signal.outcome.upper(), "")

        ts_str = signal.trade_timestamp.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

        lines = [
            f"{emoji} *{signal.severity} SIGNAL — Score {signal.score}/100*",
            "",
            f"*Market:* {signal.market_title}",
            "",
            "*Evidence:*",
        ]

        for r in reasons:
            lines.append(f"  • {r}")

        lines += [
            "",
            "*Trade details:*",
            f"  Trader:  `{signal.trader}`",
            f"  Tx:      `{signal.tx_hash}`",
            f"  Outcome: {outcome_emoji} {signal.outcome}",
            f"  Price:   {signal.trade_price:.4f}",
            f"  Size:    ${signal.trade_size_usd:,.0f}",
            f"  Time:    {ts_str}",
        ]

        return "\n".join(lines)

    def format_pair(self, signal: ScoredSignal) -> tuple[str, str]:
        """Build and return (short_msg, long_msg) with reasons populated."""
        reasons = self.build_reasons(signal)
        signal.reasons = reasons
        return (
            self.short_message(signal, reasons),
            self.long_message(signal, reasons),
        )
