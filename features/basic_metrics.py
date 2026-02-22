"""
Basic Microstructure Metrics Computation Module (Phase 2.1)

Computes the following metrics from order book state:
- Spread (already in book_state, but accessible here)
- Mid-price (already in book_state, but accessible here)
- Microprice (volume-weighted mid-price)
- Bid-Ask Imbalance
- Depth-weighted price
- Order book slope
- Cumulative depth (10 levels)
"""

from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrderBookMetrics:
    """Container for all computed order book metrics."""
    # Basic metrics (from book_state)
    mid_price: float
    spread: float
    best_bid: float
    best_ask: float
    total_bid_volume: float
    total_ask_volume: float

    # Advanced metrics (computed here)
    microprice: float
    bid_ask_imbalance: float
    depth_weighted_price: float
    bid_slope: float
    ask_slope: float
    cumulative_bid_depth: List[float]  # Cumulative volume at each level
    cumulative_ask_depth: List[float]  # Cumulative volume at each level


class MetricsCalculator:
    """
    Calculates microstructure metrics from order book state.
    Designed to be called on every order book update.
    """

    def __init__(self, depth_levels: int = 10):
        """
        Initialize the metrics calculator.

        Args:
            depth_levels: Number of price levels to consider for depth calculations
        """
        self.depth_levels = depth_levels

    def compute_microprice(
        self,
        best_bid: float,
        best_ask: float,
        best_bid_volume: float,
        best_ask_volume: float
    ) -> float:
        """
        Compute microprice (volume-weighted mid-price).

        Microprice = (best_bid × ask_volume + best_ask × bid_volume) / (bid_volume + ask_volume)

        This gives more weight to the side with less volume, reflecting
        where the price is more likely to move.
        """
        total_volume = best_bid_volume + best_ask_volume
        if total_volume == 0:
            return (best_bid + best_ask) / 2 if best_bid > 0 and best_ask < float('inf') else 0.0

        microprice = (best_bid * best_ask_volume + best_ask * best_bid_volume) / total_volume
        return microprice

    def compute_bid_ask_imbalance(
        self,
        total_bid_volume: float,
        total_ask_volume: float
    ) -> float:
        """
        Compute bid-ask imbalance.

        Imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)

        Range: [-1, 1]
        - Positive: More bid volume (buying pressure)
        - Negative: More ask volume (selling pressure)
        """
        total_volume = total_bid_volume + total_ask_volume
        if total_volume == 0:
            return 0.0

        imbalance = (total_bid_volume - total_ask_volume) / total_volume
        return imbalance

    def compute_depth_weighted_price(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]]
    ) -> float:
        """
        Compute depth-weighted average price across all visible levels.

        Weights each price level by its volume.
        """
        total_weighted_price = 0.0
        total_volume = 0.0

        for price, volume in bids:
            total_weighted_price += price * volume
            total_volume += volume

        for price, volume in asks:
            total_weighted_price += price * volume
            total_volume += volume

        if total_volume == 0:
            return 0.0

        return total_weighted_price / total_volume

    def compute_order_book_slope(
        self,
        levels: List[Tuple[float, float]],
        is_bid: bool
    ) -> float:
        """
        Compute order book slope (price sensitivity to volume).

        Slope = Δprice / Δcumulative_volume

        Higher slope means less liquidity (price moves more per unit volume).
        Lower slope means more liquidity.

        For bids: measures how much price drops as we walk down the book
        For asks: measures how much price rises as we walk up the book
        """
        if len(levels) < 2:
            return 0.0

        # Calculate cumulative volumes
        cumulative_volumes = []
        cum_vol = 0.0
        for _, volume in levels:
            cum_vol += volume
            cumulative_volumes.append(cum_vol)

        # Calculate price change from best to worst level
        if is_bid:
            # Bids: highest price first, prices decrease
            price_change = abs(levels[0][0] - levels[-1][0])
        else:
            # Asks: lowest price first, prices increase
            price_change = abs(levels[-1][0] - levels[0][0])

        volume_change = cumulative_volumes[-1] - cumulative_volumes[0] if cumulative_volumes else 0

        if volume_change == 0:
            return 0.0

        return price_change / volume_change

    def compute_cumulative_depth(
        self,
        levels: List[Tuple[float, float]],
        max_levels: int = 10
    ) -> List[float]:
        """
        Compute cumulative depth at each price level.

        Returns a list where index i contains the cumulative volume
        from best price up to and including level i.
        """
        cumulative = []
        cum_vol = 0.0

        for i, (_, volume) in enumerate(levels):
            if i >= max_levels:
                break
            cum_vol += volume
            cumulative.append(cum_vol)

        # Pad with the last cumulative value if fewer levels
        while len(cumulative) < max_levels:
            cumulative.append(cum_vol)

        return cumulative

    def compute_all_metrics(self, book_state) -> OrderBookMetrics:
        """
        Compute all metrics from an OrderBookState instance.

        Args:
            book_state: OrderBookState instance with current order book data

        Returns:
            OrderBookMetrics dataclass with all computed values
        """
        # Extract data from book_state
        # Bids: SortedDict in ascending order, we need descending (highest first)
        bids_list = [(price, qty) for price, qty in reversed(book_state.bids.items())]
        # Asks: SortedDict in ascending order (lowest first) - correct order
        asks_list = [(price, qty) for price, qty in book_state.asks.items()]

        # Get best bid/ask volumes
        best_bid_volume = bids_list[0][1] if bids_list else 0.0
        best_ask_volume = asks_list[0][1] if asks_list else 0.0

        # Compute all metrics
        microprice = self.compute_microprice(
            book_state.best_bid,
            book_state.best_ask,
            best_bid_volume,
            best_ask_volume
        )

        bid_ask_imbalance = self.compute_bid_ask_imbalance(
            book_state.total_bid_volume,
            book_state.total_ask_volume
        )

        depth_weighted_price = self.compute_depth_weighted_price(bids_list, asks_list)

        bid_slope = self.compute_order_book_slope(bids_list, is_bid=True)
        ask_slope = self.compute_order_book_slope(asks_list, is_bid=False)

        cumulative_bid_depth = self.compute_cumulative_depth(bids_list, self.depth_levels)
        cumulative_ask_depth = self.compute_cumulative_depth(asks_list, self.depth_levels)

        return OrderBookMetrics(
            mid_price=book_state.mid_price,
            spread=book_state.spread,
            best_bid=book_state.best_bid,
            best_ask=book_state.best_ask,
            total_bid_volume=book_state.total_bid_volume,
            total_ask_volume=book_state.total_ask_volume,
            microprice=microprice,
            bid_ask_imbalance=bid_ask_imbalance,
            depth_weighted_price=depth_weighted_price,
            bid_slope=bid_slope,
            ask_slope=ask_slope,
            cumulative_bid_depth=cumulative_bid_depth,
            cumulative_ask_depth=cumulative_ask_depth
        )
