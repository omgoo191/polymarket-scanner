"""
src/adapters/polygonscan.py
Watches USDC Transfer events on Polygon for trader wallet funding detection.

Data sources used (all free, no credit card):
  PRIMARY:   Polygonscan API       — token transfers, wallet tx count
             https://polygonscan.com/apis  (100k calls/day, 5 req/s)
  FALLBACK1: Moralis Web3 API      — token transfers (if Polygonscan rate-limits)
             https://moralis.io     (40k req/month free)
  FALLBACK2: Public Polygon RPC    — wallet tx count (no key needed)
             https://polygon-rpc.com

Tracks BOTH Polygon USDC contracts:
  USDC.e  0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174  (bridged, legacy Polymarket deposits)
  USDC    0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359  (native Circle-issued since 2023)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from config import load_config

logger = logging.getLogger(__name__)

POLYGONSCAN_API  = "https://api.polygonscan.com/api"
MORALIS_API_BASE = "https://deep-index.moralis.io/api/v2.2"
PUBLIC_RPC       = "https://polygon-rpc.com"

USDC_DECIMALS = 6

# Both USDC variants on Polygon — Polymarket uses both depending on deposit method
DEFAULT_USDC_CONTRACTS = [
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (bridged)
    "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",  # USDC   (native)
]


class PolygonscanAdapter:
    """
    Fetches USDC Transfer logs for given wallet addresses.
    Used to detect: "wallet funded shortly before placing large bet."

    Falls back gracefully: Polygonscan -> Moralis -> public RPC -> None
    """

    def __init__(self):
        cfg = load_config()
        self.polygonscan_key = cfg["polygonscan"]["api_key"]
        self.moralis_key     = cfg.get("moralis", {}).get("api_key", "")

        # Support both old single-contract and new list config
        raw = cfg["polygonscan"].get("usdc_contracts") or cfg["polygonscan"].get("usdc_contract")
        if raw is None:
            self.usdc_contracts = DEFAULT_USDC_CONTRACTS
        elif isinstance(raw, list):
            self.usdc_contracts = raw
        else:
            self.usdc_contracts = [raw]

        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    async def get_funding_for_wallets(
        self,
        addresses: list[str],
        lookback_minutes: int = 180,
    ) -> list[dict]:
        """
        For each address, fetch incoming USDC transfers within the lookback window.
        Returns list of normalized funding event dicts.
        """
        if not addresses:
            return []

        all_events = []
        for i in range(0, len(addresses), 3):
            batch = addresses[i : i + 3]
            tasks = [self._fetch_incoming_transfers(addr, lookback_minutes) for addr in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for addr, res in zip(batch, results):
                if isinstance(res, Exception):
                    logger.warning(f"[Chain] Funding fetch failed for {addr[:8]}: {res}")
                else:
                    all_events.extend(res)
            await asyncio.sleep(0.3)  # stay within 5 req/s

        return all_events

    async def _fetch_incoming_transfers(
        self,
        address: str,
        lookback_minutes: int,
    ) -> list[dict]:
        """Polygonscan -> Moralis fallback chain."""
        try:
            return await self._polygonscan_transfers(address, lookback_minutes)
        except _RateLimitError:
            logger.info(f"[Polygonscan] Rate-limited for {address[:8]}, trying Moralis...")
            if self.moralis_key:
                return await self._moralis_transfers(address, lookback_minutes)
            logger.debug("[Chain] No Moralis key configured; skipping fallback.")
            return []
        except Exception as e:
            logger.warning(f"[Polygonscan] Transfer fetch error for {address[:8]}: {e}")
            if self.moralis_key:
                try:
                    return await self._moralis_transfers(address, lookback_minutes)
                except Exception as e2:
                    logger.warning(f"[Moralis] Also failed for {address[:8]}: {e2}")
            return []

    # -------------------------------------------------------------------------
    # PRIMARY: Polygonscan (100k calls/day free, 5 req/s)
    # -------------------------------------------------------------------------

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def _polygonscan_transfers(self, address: str, lookback_minutes: int) -> list[dict]:
        session = await self._get_session()
        events = []
        cutoff_ts = datetime.now(tz=timezone.utc).timestamp() - lookback_minutes * 60

        for contract in self.usdc_contracts:
            params = {
                "module": "account",
                "action": "tokentx",
                "contractaddress": contract,
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "sort": "desc",
                "apikey": self.polygonscan_key,
                "offset": 50,
                "page": 1,
            }
            async with session.get(POLYGONSCAN_API, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            status  = data.get("status", "0")
            message = data.get("message", "")

            if status == "0" and "rate" in message.lower():
                raise _RateLimitError(message)
            if status != "1":
                if "No transactions" not in message:
                    logger.debug(f"[Polygonscan] {address[:8]} / {contract[:8]}: {message}")
                continue

            for tx in data.get("result", []):
                if tx.get("to", "").lower() != address.lower():
                    continue
                tx_ts = int(tx.get("timeStamp", 0))
                if tx_ts < cutoff_ts:
                    break  # sorted desc — safe early exit
                normalized = _normalize_transfer(tx, address)
                if normalized:
                    events.append(normalized)

        return _deduplicate(events)

    # -------------------------------------------------------------------------
    # FALLBACK 1: Moralis (40k req/month free, no credit card)
    # Register at moralis.io to get a free API key
    # -------------------------------------------------------------------------

    async def _moralis_transfers(self, address: str, lookback_minutes: int) -> list[dict]:
        session = await self._get_session()
        events = []
        cutoff_ts = datetime.now(tz=timezone.utc).timestamp() - lookback_minutes * 60
        headers = {"X-API-Key": self.moralis_key}

        for contract in self.usdc_contracts:
            url = f"{MORALIS_API_BASE}/{address}/erc20/transfers"
            params = {
                "chain": "polygon",
                "contract_addresses[]": contract,
                "limit": 50,
                "order": "DESC",
            }
            try:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 429:
                        logger.warning("[Moralis] Rate limited.")
                        break
                    resp.raise_for_status()
                    data = await resp.json()
            except Exception as e:
                logger.debug(f"[Moralis] Request failed: {e}")
                continue

            for tx in data.get("result", []):
                if tx.get("to_address", "").lower() != address.lower():
                    continue
                try:
                    ts = datetime.fromisoformat(tx["block_timestamp"].replace("Z", "+00:00"))
                except Exception:
                    continue
                if ts.timestamp() < cutoff_ts:
                    break
                amount_usd = int(tx.get("value", 0)) / (10 ** USDC_DECIMALS)
                if amount_usd < 100:
                    continue
                events.append({
                    "tx_hash":      tx.get("transaction_hash", ""),
                    "to_address":   address.lower(),
                    "from_address": tx.get("from_address", "").lower(),
                    "amount_usd":   round(amount_usd, 4),
                    "timestamp":    ts,
                    "source":       "moralis",
                })
            await asyncio.sleep(0.1)

        return _deduplicate(events)

    # -------------------------------------------------------------------------
    # Wallet metadata helpers
    # -------------------------------------------------------------------------

    async def get_wallet_age_days(self, address: str) -> Optional[float]:
        """Estimate wallet age from first tx. Polygonscan only (no free RPC equivalent)."""
        session = await self._get_session()
        try:
            params = {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "sort": "asc",
                "page": 1,
                "offset": 1,
                "apikey": self.polygonscan_key,
            }
            async with session.get(POLYGONSCAN_API, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            if data.get("status") == "1":
                txs = data.get("result", [])
                if txs:
                    first_ts = int(txs[0].get("timeStamp", 0))
                    if first_ts:
                        age_s = datetime.now(tz=timezone.utc).timestamp() - first_ts
                        return age_s / 86400
        except Exception as e:
            logger.debug(f"[Polygonscan] Wallet age failed for {address[:8]}: {e}")
        return None

    async def get_transaction_count(self, address: str) -> Optional[int]:
        """
        Return on-chain tx count.
        PRIMARY: Polygonscan proxy  FALLBACK: public polygon-rpc.com (no key needed)
        """
        session = await self._get_session()

        # Polygonscan
        try:
            params = {
                "module": "proxy",
                "action": "eth_getTransactionCount",
                "address": address,
                "tag": "latest",
                "apikey": self.polygonscan_key,
            }
            async with session.get(POLYGONSCAN_API, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
            hex_count = data.get("result", "0x0")
            if hex_count and hex_count != "0x0":
                return int(hex_count, 16)
        except Exception as e:
            logger.debug(f"[Polygonscan] Tx count failed for {address[:8]}: {e} — trying public RPC")

        # FALLBACK 2: public Polygon JSON-RPC — no API key, always free
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionCount",
                "params": [address, "latest"],
                "id": 1,
            }
            async with session.post(PUBLIC_RPC, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
            return int(data.get("result", "0x0"), 16)
        except Exception as e:
            logger.debug(f"[PublicRPC] Tx count failed for {address[:8]}: {e}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

class _RateLimitError(Exception):
    pass


def _normalize_transfer(tx: dict, address: str) -> Optional[dict]:
    try:
        amount_usd = int(tx.get("value", 0)) / (10 ** USDC_DECIMALS)
        if amount_usd < 100:
            return None
        return {
            "tx_hash":      tx["hash"],
            "to_address":   address.lower(),
            "from_address": tx.get("from", "").lower(),
            "amount_usd":   round(amount_usd, 4),
            "timestamp":    datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc),
            "source":       "polygonscan",
        }
    except Exception:
        return None


def _deduplicate(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for e in events:
        key = e.get("tx_hash", "")
        if key and key not in seen:
            seen.add(key)
            out.append(e)
    return out
