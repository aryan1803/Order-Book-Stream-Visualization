import httpx
import asyncio
import sys
import os
import logging

logger = logging.getLogger(__name__)

# Add the project root to the sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import BINANCE_REST_API_BASE_URL, SNAPSHOT_LIMIT

async def fetch_depth_snapshot(symbol: str):
    """
    Fetches the depth snapshot for a given symbol from Binance REST API.
    """
    url = f"{BINANCE_REST_API_BASE_URL}/depth"
    params = {
        "symbol": symbol.upper(),
        "limit": SNAPSHOT_LIMIT # Max 1000 for /fapi/v1/depth
    }
    logger.debug(f"[{symbol}] Preparing to fetch depth snapshot from {url} with params: {params}")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            response.raise_for_status()  # Raise an exception for HTTP errors
            data = response.json()
            logger.info(f"[{symbol}] Successfully fetched snapshot.")
            return data
    except httpx.HTTPStatusError as e:
        logger.error(f"[{symbol}] HTTP error fetching snapshot: {e}", exc_info=True)
        return None
    except httpx.RequestError as e:
        logger.error(f"[{symbol}] Request error fetching snapshot: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.critical(f"[{symbol}] An unexpected error occurred fetching snapshot: {e}", exc_info=True)
        return None

async def main():
    """
    Main function to test fetching snapshots.
    """
    # For testing, fetch for btcusdt
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    snapshot = await fetch_depth_snapshot("btcusdt")
    if snapshot:
        logger.info(f"Snapshot received for btcusdt. Last update ID: {snapshot.get('lastUpdateId')}")
        logger.info(f"Bids count: {len(snapshot.get('bids', []))}")
        logger.info(f"Asks count: {len(snapshot.get('asks', []))}")
    else:
        logger.warning("Failed to fetch snapshot for btcusdt.")

if __name__ == "__main__":
    asyncio.run(main())
