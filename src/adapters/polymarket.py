"""
src/adapters/polymarket.py
Fetches markets and trades from Polymarket.
Primary: REST API (gamma-api.polymarket.com)
Fallback: CLOB API (clob.polymarket.com)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from config import load_config

logger = logging.getLogger(__name__)

# USDC decimals on Polygon
USDC_DECIMALS = 6


class PolymarketAdapter:
    """
    Fetches:
    - Active markets (with insider-risk tagging)
    - Recent trades for those markets
    - Price snapshots (best-bid / last-traded)
    """

    def __init__(self):
        cfg = load_config()
        self.rest_base = cfg["polymarket"]["rest_base_url"].rstrip("/")
        self.clob_base = cfg["polymarket"]["clob_base_url"].rstrip("/")
        self.markets_limit = cfg["polymarket"].get("markets_limit", 100)
        self.insider_keywords: list[str] = [
            kw.lower() for kw in cfg.get("insider_keywords", [])
        ]
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Markets
    # ─────────────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_markets(self) -> list[dict]:
        """Return normalized market records with insider_risk flag."""
        markets = []

        # Try REST first
        try:
            markets = await self._fetch_markets_rest()
            logger.info(f"[Polymarket] Fetched {len(markets)} markets via REST")
        except Exception as e:
            logger.warning(f"[Polymarket] REST failed ({e}), trying CLOB GraphQL")
            markets = await self._fetch_markets_clob()
            logger.info(f"[Polymarket] Fetched {len(markets)} markets via CLOB")

        # Tag insider-risk
        tagged = []
        for m in markets:
            m["insider_risk"] = self._is_insider_risk(m.get("title", ""))
            tagged.append(m)

        insider_count = sum(1 for m in tagged if m["insider_risk"])
        logger.info(f"[Polymarket] {insider_count} insider-risk markets identified")
        return tagged

    async def _fetch_markets_rest(self) -> list[dict]:
        session = await self._get_session()
        markets = []
        offset = 0

        while True:
            url = f"{self.rest_base}/markets"
            params = {
                "active": "true",
                "limit": self.markets_limit,
                "offset": offset,
            }
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            # gamma API returns list directly or {"data": [...]}
            if isinstance(data, list):
                batch = data
            else:
                batch = data.get("data", data)

            if not batch:
                break

            for item in batch:
                markets.append(self._normalize_market_rest(item))

            if len(batch) < self.markets_limit:
                break
            offset += self.markets_limit
            await asyncio.sleep(0.2)  # be polite

        return markets

    def _normalize_market_rest(self, raw: dict) -> dict:
        end_time = None
        for field in ("endDate", "end_date", "endDateIso", "end_time"):
            if raw.get(field):
                try:
                    end_time = datetime.fromisoformat(
                        str(raw[field]).replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
                break

        return {
            "id": str(raw.get("id", raw.get("conditionId", ""))),
            "title": raw.get("question", raw.get("title", "")),
            "slug": raw.get("slug", ""),
            "end_time": end_time,
            "is_active": bool(raw.get("active", True)),
        }

    async def _fetch_markets_clob(self) -> list[dict]:
        """GraphQL fallback via CLOB API."""
        session = await self._get_session()
        url = f"{self.clob_base}/markets"
        params = {"limit": self.markets_limit}
        markets = []

        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        items = data if isinstance(data, list) else data.get("data", [])
        for item in items:
            markets.append({
                "id": str(item.get("condition_id", item.get("id", ""))),
                "title": item.get("question", item.get("title", "")),
                "slug": item.get("market_slug", ""),
                "end_time": None,
                "is_active": True,
            })
        return markets

    def _is_insider_risk(self, title: str) -> bool:
        title_lower = title.lower()
        return any(kw in title_lower for kw in self.insider_keywords)

    # ─────────────────────────────────────────────────────────────────────────
    # Trades
    # ─────────────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_trades(
        self,
        market_id: str,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        """Fetch recent trades for a market. Returns normalized trade dicts."""
        try:
            trades = await self._fetch_trades_clob(market_id, since)
        except Exception as e:
            logger.warning(f"[Polymarket] CLOB trades failed for {market_id}: {e}")
            trades = []

        return trades

    async def _fetch_trades_clob(
        self,
        market_id: str,
        since: Optional[datetime],
    ) -> list[dict]:
        session = await self._get_session()
        url = f"{self.clob_base}/trades"
        params = {"market": market_id, "limit": 50}
        if since:
            params["after"] = int(since.timestamp())

        async with session.get(url, params=params) as resp:
            if resp.status == 404:
                return []
            resp.raise_for_status()
            data = await resp.json()

        trades_raw = data if isinstance(data, list) else data.get("data", [])
        result = []
        for raw in trades_raw:
            t = self._normalize_trade(raw, market_id)
            if t:
                result.append(t)
        return result

    def _normalize_trade(self, raw: dict, market_id: str) -> Optional[dict]:
        """Normalize a raw CLOB trade record."""
        try:
            # Size: CLOB gives "size" in shares, "price" is 0–1 prob
            price = float(raw.get("price", 0))
            size_shares = float(raw.get("size", raw.get("amount", 0)))

            # notional = shares * price (in USDC)
            # For Polymarket, 1 share = $1 max, so notional ≈ shares * price
            size_usd = size_shares * price if price > 0 else size_shares

            # Some endpoints give raw USDC (6 decimals)
            if size_usd > 1_000_000:
                size_usd = size_usd / (10 ** USDC_DECIMALS)

            ts_raw = raw.get("timestamp", raw.get("created_at", raw.get("time")))
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))

            trader = raw.get("maker", raw.get("taker", raw.get("trader", "")))
            if not trader:
                return None

            return {
                "tx_hash": raw.get("transactionHash", raw.get("id", raw.get("hash", ""))),
                "market_id": market_id,
                "trader": trader.lower(),
                "outcome": raw.get("outcome", raw.get("token_id", "YES")),
                "side": raw.get("side", "BUY").upper(),
                "size_usd": round(size_usd, 4),
                "price": round(price, 6),
                "timestamp": ts,
                "raw": raw,
            }
        except Exception as e:
            logger.debug(f"[Polymarket] Could not normalize trade: {e} | raw={raw}")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Price snapshots
    # ─────────────────────────────────────────────────────────────────────────

    async def fetch_price_snapshot(self, market_id: str) -> list[dict]:
        """Return current best prices per outcome token."""
        session = await self._get_session()
        try:
            url = f"{self.clob_base}/book"
            params = {"token_id": market_id}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()

            snapshots = []
            for side, key in [("asks", "YES"), ("bids", "NO")]:
                entries = data.get(side, [])
                if entries:
                    best = entries[0]
                    snapshots.append({
                        "market_id": market_id,
                        "outcome": key,
                        "price": float(best.get("price", 0)),
                    })
            return snapshots
        except Exception as e:
            logger.debug(f"[Polymarket] Price snapshot failed for {market_id}: {e}")
            return []
