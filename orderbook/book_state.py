from sortedcontainers import SortedDict
from typing import Dict, List, Tuple
import sys
import os
import asyncio
import logging # Added this import

logger = logging.getLogger(__name__) # Added this line


# Add the project root to the sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import ORDER_BOOK_DEPTH_LIMIT, SYMBOLS
from data_ingestion.snapshot_loader import fetch_depth_snapshot


class OrderBookState:
    def __init__(self, symbol: str):
        self.symbol = symbol
        # Bids are sorted descending by price
        self.bids: SortedDict[float, float] = SortedDict()
        # Asks are sorted ascending by price
        self.asks: SortedDict[float, float] = SortedDict()
        self.last_update_id = -1
        self.sequence_number = -1 # This will be used for tracking 'u' or 'U'

        # Derived metrics
        self._best_bid = 0.0
        self._best_ask = float('inf')
        self._mid_price = 0.0
        self._spread = float('inf')
        self._total_bid_volume = 0.0
        self._total_ask_volume = 0.0

    def initialize_with_snapshot(self, snapshot: Dict):
        """
        Initializes the order book with a full snapshot.
        """
        self.bids.clear()
        self.asks.clear()

        for price_str, quantity_str in snapshot['bids']:
            price = float(price_str)
            quantity = float(quantity_str)
            # Store bids in descending order
            self.bids[price] = quantity

        for price_str, quantity_str in snapshot['asks']:
            price = float(price_str)
            quantity = float(quantity_str)
            # Store asks in ascending order
            self.asks[price] = quantity

        self.last_update_id = snapshot['lastUpdateId']
        # For Binance, the lastUpdateId from the REST API snapshot should be treated as 'u'
        self.sequence_number = snapshot['lastUpdateId']

        self._maintain_top_levels()
        self._update_derived_metrics()
        logger.info(f"[{self.symbol}] Initialized with snapshot. Last Update ID: {self.last_update_id}") # Changed print to logger.info

    def apply_diff(self, diff: Dict):
        """
        Applies a WebSocket diff update to the order book.
        """
        # Ensure diffs are applied sequentially
        # The 'u' in diff represents the final update ID of the event
        # The 'U' in diff represents the first update ID of the event
        # Logic: firstUpdateId <= lastUpdateId AND lastUpdateId <= finalUpdateId
        # Or more simply: snapshot lastUpdateId < U (first update id in event)
        # And next event's U should be sequence_number + 1
        # And the new sequence_number becomes the event's u
        
        # For now, let's apply the diff directly without sequence checks
        # Sequence checks will be implemented in sequence_validator.py
        
        if diff['u'] <= self.last_update_id:
            # This diff is older than our current snapshot or last applied diff, ignore
            return

        # Update bids
        for price_str, quantity_str in diff['b']:
            price = float(price_str)
            quantity = float(quantity_str)
            if quantity == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = quantity

        # Update asks
        for price_str, quantity_str in diff['a']:
            price = float(price_str)
            quantity = float(quantity_str)
            if quantity == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = quantity
        
        self.last_update_id = diff['u']
        self.sequence_number = diff['u'] # Assuming 'u' is the relevant sequence for now.

        self._maintain_top_levels()
        self._update_derived_metrics()
    
    def _maintain_top_levels(self):
        """
        Keeps only the top N levels for bids and asks.
        Bids are highest price first, asks are lowest price first.
        """
        # SortedDict stores items in ascending key order by default.
        # For bids, we want descending prices, so we iterate from the end (highest price).
        # For asks, we want ascending prices, so we iterate from the beginning (lowest price).
        
        # Remove bids with lowest prices (from beginning of SortedDict) until limit
        while len(self.bids) > ORDER_BOOK_DEPTH_LIMIT:
            del self.bids[self.bids.keys()[0]] # Remove the lowest bid price

        # Remove asks with highest prices (from end of SortedDict) until limit
        while len(self.asks) > ORDER_BOOK_DEPTH_LIMIT:
            del self.asks[self.asks.keys()[-1]] # Remove the highest ask price


    def _update_derived_metrics(self):
        """
        Updates best_bid, best_ask, mid_price, spread, total_bid_volume, total_ask_volume.
        Also checks for Bids < Asks invariant.
        """
        self._total_bid_volume = sum(self.bids.values())
        self._total_ask_volume = sum(self.asks.values())

        if self.bids:
            self._best_bid = self.bids.keys()[-1] # Highest bid price
        else:
            self._best_bid = 0.0

        if self.asks:
            self._best_ask = self.asks.keys()[0] # Lowest ask price
        else:
            self._best_ask = float('inf')

        # Validate Bids < Asks invariant (spread always positive)
        if self._best_bid >= self._best_ask:
            logger.warning(f"[{self.symbol}] Invariant Violation: Best Bid ({self._best_bid}) >= Best Ask ({self._best_ask}). Order book is crossed or invalid.")
            # Set mid_price and spread to indicate an invalid state, consistent with previous behavior
            self._mid_price = 0.0
            self._spread = float('inf')
        elif self._best_bid > 0.0 and self._best_ask != float('inf'):
            self._mid_price = (self._best_bid + self._best_ask) / 2
            self._spread = self._best_ask - self._best_bid
        else:
            # One side of the book is empty, so mid_price and spread are undefined
            self._mid_price = 0.0
            self._spread = float('inf')

    @property
    def best_bid(self) -> float:
        return self._best_bid

    @property
    def best_ask(self) -> float:
        return self._best_ask

    @property
    def mid_price(self) -> float:
        return self._mid_price

    @property
    def spread(self) -> float:
        return self._spread

    @property
    def total_bid_volume(self) -> float:
        return self._total_bid_volume

    @property
    def total_ask_volume(self) -> float:
        return self._total_ask_volume

    def get_order_book(self) -> Dict:
        """Returns the current state of the order book as a dictionary."""
        return {
            "symbol": self.symbol,
            "last_update_id": self.last_update_id,
            "bids": [[price, qty] for price, qty in self.bids.items()],
            "asks": [[price, qty] for price, qty in self.asks.items()],
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "total_bid_volume": self.total_bid_volume,
            "total_ask_volume": self.total_ask_volume,
        }

    def __str__(self):
        return (f"OrderBookState(Symbol: {self.symbol}, "
                f"LastUpdateId: {self.last_update_id}, "
                f"Best Bid: {self.best_bid}, Best Ask: {self.best_ask}, "
                f"Mid Price: {self.mid_price}, Spread: {self.spread})")

# Example usage (for testing purposes, can be removed later)
async def main():
    symbol = SYMBOLS[0]
    book = OrderBookState(symbol)

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    logger.info(f"Initializing order book for {symbol}...") # Changed print to logger.info
    snapshot = await fetch_depth_snapshot(symbol)
    if snapshot:
        book.initialize_with_snapshot(snapshot)
        logger.info(book) # Changed print to logger.info

        # Simulate a diff update
        logger.info("\nSimulating diff update...") # Changed print to logger.info
        sample_diff = {
            "e": "depthUpdate",
            "E": 1678881234567,
            "s": symbol.upper(),
            "U": book.last_update_id + 1, # First update ID in event
            "u": book.last_update_id + 5, # Final update ID in event
            "b": [["67172.2", "0.5"], ["67170.0", "0.0"]], # Update 67172.2, remove 67170.0 (if present)
            "a": [["67172.3", "0.7"], ["67175.0", "0.0"]]  # Update 67172.3, remove 67175.0 (if present)
        }
        # A simple check to prevent applying diffs with 'u' <= last_update_id
        # This will be handled robustly in sequence_validator
        if sample_diff['u'] > book.last_update_id:
            book.apply_diff(sample_diff)
            logger.info(book) # Changed print to logger.info
            logger.info("Current bids: %s", book.get_order_book()['bids']) # Changed print to logger.info
            logger.info("Current asks: %s", book.get_order_book()['asks']) # Changed print to logger.info
        else:
            logger.info("Simulated diff is outdated, not applied.") # Changed print to logger.info
    else:
        logger.warning(f"Could not fetch snapshot for {symbol}.") # Changed print to logger.warning

if __name__ == "__main__":
    asyncio.run(main())
