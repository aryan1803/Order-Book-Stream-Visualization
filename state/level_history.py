"""
Level History Buffer Module (Phase 4)

Stores 180 seconds of order book price-level snapshots for:
- Liquidity Heatmap (X: time, Y: price, Color: size)
- 3D Surface Plot (X: time, Y: price, Z: size)
- Depth Curve (cumulative bid/ask from latest snapshot)

Each second, the current top-10 bid/ask levels are captured as a LevelSnapshot.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Tuple, Optional
import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class LevelSnapshot:
    """A single point-in-time snapshot of order book levels."""
    timestamp: float
    bid_levels: List[Tuple[float, float]]  # [(price, size), ...] highest-first
    ask_levels: List[Tuple[float, float]]  # [(price, size), ...] lowest-first
    mid_price: float


class LevelHistoryBuffer:
    """
    Rolling buffer storing 180 seconds of order book level snapshots.

    Used by the dashboard to render heatmap, depth curve, and 3D surface charts.
    Thread safety: Relies on CPython GIL for atomic deque.append and list(deque).
    """

    def __init__(self, window_seconds: int = 180):
        self.window_seconds = window_seconds
        self._buffer: Deque[LevelSnapshot] = deque(maxlen=window_seconds)

    def update(self, book_state) -> None:
        """
        Snapshot the current order book levels into the history.

        Args:
            book_state: OrderBookState instance with .bids, .asks SortedDicts
        """
        # Bids: SortedDict ascending, take last 10 and reverse for highest-first
        bids = [(float(price), float(qty))
                for price, qty in reversed(list(book_state.bids.items())[-10:])]

        # Asks: SortedDict ascending, take first 10 (lowest-first)
        asks = [(float(price), float(qty))
                for price, qty in list(book_state.asks.items())[:10]]

        snapshot = LevelSnapshot(
            timestamp=time.time(),
            bid_levels=bids,
            ask_levels=asks,
            mid_price=book_state.mid_price,
        )
        self._buffer.append(snapshot)

    def get_heatmap_matrix(self) -> Tuple[List[float], List[float], List[List[float]]]:
        """
        Build a 2D grid for go.Heatmap.

        Returns:
            (timestamps, price_axis, z_matrix) where:
            - timestamps: list of unix timestamps (length = buffer size)
            - price_axis: sorted list of all unique prices seen in window
            - z_matrix: list of rows (one per timestamp), each row has one value
              per price. Bid sizes are positive, ask sizes are negative.
        """
        snapshots = list(self._buffer)
        if not snapshots:
            return [], [], [[]]

        # Collect all unique prices across the window
        all_prices = set()
        for snap in snapshots:
            for price, _ in snap.bid_levels:
                all_prices.add(price)
            for price, _ in snap.ask_levels:
                all_prices.add(price)

        price_axis = sorted(all_prices)
        price_to_idx = {p: i for i, p in enumerate(price_axis)}
        n_prices = len(price_axis)

        timestamps = []
        z_matrix = []

        for snap in snapshots:
            timestamps.append(snap.timestamp)
            row = [0.0] * n_prices

            # Bids as positive values
            for price, size in snap.bid_levels:
                row[price_to_idx[price]] = size

            # Asks as negative values (for diverging colorscale)
            for price, size in snap.ask_levels:
                row[price_to_idx[price]] = -size

            z_matrix.append(row)

        return timestamps, price_axis, z_matrix

    def get_depth_curve(self) -> Tuple[List[float], List[float], List[float], List[float]]:
        """
        Return cumulative bid/ask depth from the latest snapshot.

        Returns:
            (bid_prices, bid_cum_sizes, ask_prices, ask_cum_sizes)
            - bid_prices: ascending order (lowest to highest)
            - bid_cum_sizes: cumulative size from outermost to innermost bid
            - ask_prices: ascending order (lowest to highest)
            - ask_cum_sizes: cumulative size from innermost to outermost ask
        """
        if not self._buffer:
            return [], [], [], []

        latest = self._buffer[-1]

        # Bids: highest-first in snapshot. Build cumulative from best bid outward.
        bid_prices = []
        bid_cum = []
        cum = 0.0
        for price, size in latest.bid_levels:
            cum += size
            bid_prices.append(price)
            bid_cum.append(cum)
        # Reverse so prices are ascending (outermost bid first, best bid last)
        bid_prices.reverse()
        bid_cum.reverse()

        # Asks: lowest-first in snapshot. Build cumulative from best ask outward.
        ask_prices = []
        ask_cum = []
        cum = 0.0
        for price, size in latest.ask_levels:
            cum += size
            ask_prices.append(price)
            ask_cum.append(cum)

        return bid_prices, bid_cum, ask_prices, ask_cum

    def get_dom_ladder(self) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], float]:
        """
        Return current bid/ask levels for the DOM ladder display.

        Returns:
            (bid_levels, ask_levels, mid_price)
            - bid_levels: [(price, size), ...] highest-first (best bid first)
            - ask_levels: [(price, size), ...] lowest-first (best ask first)
            - mid_price: current mid price
        """
        if not self._buffer:
            return [], [], 0.0

        latest = self._buffer[-1]
        return latest.bid_levels, latest.ask_levels, latest.mid_price

    def get_volume_profile(self) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """
        Aggregate average liquidity at each price level across the rolling window.

        Returns:
            (bid_profile, ask_profile) where each is a list of (price, avg_size)
            sorted by price ascending.
        """
        snapshots = list(self._buffer)
        if not snapshots:
            return [], []

        # Accumulate total size and count at each price
        bid_totals = {}  # price -> [total_size, count]
        ask_totals = {}

        for snap in snapshots:
            for price, size in snap.bid_levels:
                if price not in bid_totals:
                    bid_totals[price] = [0.0, 0]
                bid_totals[price][0] += size
                bid_totals[price][1] += 1

            for price, size in snap.ask_levels:
                if price not in ask_totals:
                    ask_totals[price] = [0.0, 0]
                ask_totals[price][0] += size
                ask_totals[price][1] += 1

        # Compute averages, sorted by price ascending
        bid_profile = sorted(
            [(price, total / count) for price, (total, count) in bid_totals.items()],
            key=lambda x: x[0],
        )
        ask_profile = sorted(
            [(price, total / count) for price, (total, count) in ask_totals.items()],
            key=lambda x: x[0],
        )

        return bid_profile, ask_profile

    def get_surface_data(self) -> Tuple[List[float], List[float], List[List[float]]]:
        """
        Build arrays for go.Surface (same data as heatmap but absolute sizes).

        Returns:
            (timestamps, price_axis, z_matrix) with all Z values as abs(size).
        """
        timestamps, price_axis, z_matrix = self.get_heatmap_matrix()

        z_abs = [[abs(v) for v in row] for row in z_matrix]
        return timestamps, price_axis, z_abs

    @property
    def size(self) -> int:
        """Current number of snapshots in the buffer."""
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        """Check if buffer has reached its maximum size."""
        return len(self._buffer) >= self.window_seconds
