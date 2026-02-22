"""
Rolling State Buffer Module (Phase 2.3)

In-memory rolling buffer for storing aggregated metrics:
- 180 seconds window
- 1-second aggregation
- No disk persistence

Stores per timestamp:
- timestamp
- mid_price
- spread
- imbalance (bid-ask imbalance)
- ofi (order flow imbalance)
- total_bid_volume
- total_ask_volume
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Dict
import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class AggregatedState:
    """Container for 1-second aggregated state."""
    timestamp: float  # Unix timestamp (seconds)
    mid_price: float
    spread: float
    imbalance: float  # Bid-ask imbalance
    ofi: float  # Order flow imbalance for this second
    total_bid_volume: float
    total_ask_volume: float

    # Optional additional metrics
    microprice: Optional[float] = None
    update_count: int = 0  # Number of updates in this second


@dataclass
class AccumulatorState:
    """Accumulator for building 1-second aggregates."""
    sum_mid_price: float = 0.0
    sum_spread: float = 0.0
    sum_imbalance: float = 0.0
    sum_ofi: float = 0.0
    sum_bid_volume: float = 0.0
    sum_ask_volume: float = 0.0
    sum_microprice: float = 0.0
    update_count: int = 0

    # Last values for snapshot
    last_mid_price: float = 0.0
    last_spread: float = 0.0
    last_imbalance: float = 0.0
    last_bid_volume: float = 0.0
    last_ask_volume: float = 0.0
    last_microprice: float = 0.0

    def reset(self):
        """Reset all accumulators."""
        self.sum_mid_price = 0.0
        self.sum_spread = 0.0
        self.sum_imbalance = 0.0
        self.sum_ofi = 0.0
        self.sum_bid_volume = 0.0
        self.sum_ask_volume = 0.0
        self.sum_microprice = 0.0
        self.update_count = 0


class RollingBuffer:
    """
    Rolling buffer for storing time-series metrics data.

    Maintains a fixed-size window of 1-second aggregated data points.
    """

    def __init__(self, window_seconds: int = 180):
        """
        Initialize the rolling buffer.

        Args:
            window_seconds: Size of the rolling window in seconds (default: 180)
        """
        self.window_seconds = window_seconds
        self._buffer: Deque[AggregatedState] = deque(maxlen=window_seconds)
        self._accumulator = AccumulatorState()
        self._current_second: int = 0
        self._is_initialized: bool = False

    def _get_current_second(self) -> int:
        """Get current time truncated to second."""
        return int(time.time())

    def update(
        self,
        mid_price: float,
        spread: float,
        imbalance: float,
        ofi: float,
        total_bid_volume: float,
        total_ask_volume: float,
        microprice: Optional[float] = None
    ):
        """
        Add a new data point to the buffer.

        Data is accumulated until the second changes, then aggregated and stored.

        Args:
            mid_price: Current mid price
            spread: Current spread
            imbalance: Current bid-ask imbalance
            ofi: Order flow imbalance for this update
            total_bid_volume: Current total bid volume
            total_ask_volume: Current total ask volume
            microprice: Optional microprice
        """
        current_second = self._get_current_second()

        if not self._is_initialized:
            self._current_second = current_second
            self._is_initialized = True

        # Check if we've moved to a new second
        if current_second != self._current_second:
            # Finalize the previous second's data
            self._finalize_second(self._current_second)
            self._current_second = current_second

        # Accumulate data for current second
        self._accumulator.sum_mid_price += mid_price
        self._accumulator.sum_spread += spread
        self._accumulator.sum_imbalance += imbalance
        self._accumulator.sum_ofi += ofi  # OFI is summed (not averaged)
        self._accumulator.sum_bid_volume += total_bid_volume
        self._accumulator.sum_ask_volume += total_ask_volume
        if microprice is not None:
            self._accumulator.sum_microprice += microprice
        self._accumulator.update_count += 1

        # Store last values
        self._accumulator.last_mid_price = mid_price
        self._accumulator.last_spread = spread
        self._accumulator.last_imbalance = imbalance
        self._accumulator.last_bid_volume = total_bid_volume
        self._accumulator.last_ask_volume = total_ask_volume
        self._accumulator.last_microprice = microprice if microprice else 0.0

    def _finalize_second(self, timestamp: int):
        """Finalize and store the aggregated data for a second."""
        if self._accumulator.update_count == 0:
            return

        count = self._accumulator.update_count

        # Use last values for price/volume (point-in-time snapshot)
        # Use sum for OFI (cumulative over the second)
        # Use average for imbalance
        aggregated = AggregatedState(
            timestamp=float(timestamp),
            mid_price=self._accumulator.last_mid_price,
            spread=self._accumulator.last_spread,
            imbalance=self._accumulator.sum_imbalance / count,
            ofi=self._accumulator.sum_ofi,  # Sum of OFI over the second
            total_bid_volume=self._accumulator.last_bid_volume,
            total_ask_volume=self._accumulator.last_ask_volume,
            microprice=self._accumulator.last_microprice,
            update_count=count
        )

        self._buffer.append(aggregated)
        self._accumulator.reset()

    def flush(self):
        """Force finalization of current accumulator state."""
        if self._accumulator.update_count > 0:
            self._finalize_second(self._current_second)

    def get_latest(self) -> Optional[AggregatedState]:
        """Get the most recent aggregated state."""
        if self._buffer:
            return self._buffer[-1]
        return None

    def get_all(self) -> List[AggregatedState]:
        """Get all data points in the buffer."""
        return list(self._buffer)

    def get_window(self, seconds: int) -> List[AggregatedState]:
        """
        Get the last N seconds of data.

        Args:
            seconds: Number of seconds to retrieve

        Returns:
            List of AggregatedState objects
        """
        if seconds >= len(self._buffer):
            return list(self._buffer)
        return list(self._buffer)[-seconds:]

    def get_mid_prices(self) -> List[float]:
        """Get all mid prices in the buffer."""
        return [state.mid_price for state in self._buffer]

    def get_ofi_series(self) -> List[float]:
        """Get all OFI values in the buffer."""
        return [state.ofi for state in self._buffer]

    def get_imbalance_series(self) -> List[float]:
        """Get all imbalance values in the buffer."""
        return [state.imbalance for state in self._buffer]

    def get_timestamps(self) -> List[float]:
        """Get all timestamps in the buffer."""
        return [state.timestamp for state in self._buffer]

    @property
    def size(self) -> int:
        """Current number of data points in the buffer."""
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        """Check if buffer has reached its maximum size."""
        return len(self._buffer) >= self.window_seconds

    def clear(self):
        """Clear all data from the buffer."""
        self._buffer.clear()
        self._accumulator.reset()
        self._is_initialized = False

    def to_dict(self) -> Dict:
        """
        Export buffer data as a dictionary.

        Useful for JSON serialization or API responses.
        """
        return {
            "window_seconds": self.window_seconds,
            "size": self.size,
            "data": [
                {
                    "timestamp": state.timestamp,
                    "mid_price": state.mid_price,
                    "spread": state.spread,
                    "imbalance": state.imbalance,
                    "ofi": state.ofi,
                    "total_bid_volume": state.total_bid_volume,
                    "total_ask_volume": state.total_ask_volume,
                    "microprice": state.microprice,
                    "update_count": state.update_count
                }
                for state in self._buffer
            ]
        }
