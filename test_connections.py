#!/usr/bin/env python3
"""
scripts/test_connections.py
Validates all external connections before running the radar.
Usage: python scripts/test_connections.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger("test")


async def test_telegram():
    from src.notifications.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    ok = await notifier.test_connection()
    print(f"  Telegram:    {'✅ OK' if ok else '❌ FAILED'}")
    return ok


async def test_polymarket():
    from src.adapters.polymarket import PolymarketAdapter
    adapter = PolymarketAdapter()
    try:
        markets = await adapter.fetch_markets()
        insider = [m for m in markets if m.get("insider_risk")]
        print(f"  Polymarket:  ✅ OK — {len(markets)} markets, {len(insider)} insider-risk")
        await adapter.close()
        return True
    except Exception as e:
        print(f"  Polymarket:  ❌ FAILED — {e}")
        await adapter.close()
        return False


async def test_polygonscan():
    from src.adapters.polygonscan import PolygonscanAdapter
    adapter = PolygonscanAdapter()
    # Test with a known active address (Polymarket's USDC proxy)
    test_addr = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    try:
        age = await adapter.get_wallet_age_days(test_addr)
        print(f"  Polygonscan: ✅ OK — wallet age lookup returned {age:.1f} days" if age else "  Polygonscan: ✅ OK (no history for test wallet)")
        await adapter.close()
        return True
    except Exception as e:
        print(f"  Polygonscan: ❌ FAILED — {e}")
        await adapter.close()
        return False


async def test_db():
    from src.db.session import create_all_tables, dispose
    try:
        await create_all_tables()
        await dispose()
        print("  Database:    ✅ OK")
        return True
    except Exception as e:
        print(f"  Database:    ❌ FAILED — {e}")
        return False


async def main():
    print("\n🔍 Testing all connections…\n")
    results = await asyncio.gather(
        test_telegram(),
        test_polymarket(),
        test_polygonscan(),
        test_db(),
        return_exceptions=True,
    )
    all_ok = all(r is True for r in results)
    print(f"\n{'✅ All systems go!' if all_ok else '⚠️ Some checks failed — review above.'}\n")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
