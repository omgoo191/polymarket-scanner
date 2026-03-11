#!/usr/bin/env python3
"""
scripts/migrate.py
Run once to create all DB tables.
Usage: python scripts/migrate.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.session import create_all_tables, dispose
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate")


async def main():
    logger.info("Running migrations…")
    await create_all_tables()
    await dispose()
    logger.info("✅ Tables created successfully.")


if __name__ == "__main__":
    asyncio.run(main())
