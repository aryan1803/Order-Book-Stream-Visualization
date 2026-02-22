"""
Order Flow Imbalance (OFI) Tracking Module (Phase 2.2)

Tracks volume changes per update:
- ΔBidVolume: Change in total bid volume
- ΔAskVolume: Change in total ask volume
- OFI = ΔBidVolume − ΔAskVolume

OFI is a measure of order flow imbalance:
- Positive OFI: Net buying pressure (bids increasing relative to asks)
- Negative OFI: Net selling pressure (asks increasing relative to bids)
"""

from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class OrderFlowMetrics:
    """Container for order flow metrics."""
    delta_bid_volume: float  # Change in bid volume since last update
    delta_ask_volume: float  # Change in ask volume since last update
    ofi: float  # Order Flow Imbalance = ΔBidVolume - ΔAskVolume
    cumulative_ofi: float  # Cumulative OFI over the tracking period


class OrderFlowTracker:
    """
    Tracks order flow by monitoring volume changes between updates.

    Usage:
        tracker = OrderFlowTracker()
        # On each order book update:
        metrics = tracker.update(book_state.total_bid_volume, book_state.total_ask_volume)
    """

    def __init__(self):
        """Initialize the order flow tracker."""
        self._prev_bid_volume: Optional[float] = None
        self._prev_ask_volume: Optional[float] = None
        self._cumulative_ofi: float = 0.0
        self._is_initialized: bool = False

    def reset(self):
        """Reset the tracker state."""
        self._prev_bid_volume = None
        self._prev_ask_volume = None
        self._cumulative_ofi = 0.0
        self._is_initialized = False

    def reset_cumulative_ofi(self):
        """Reset only the cumulative OFI (useful for periodic resets)."""
        self._cumulative_ofi = 0.0

    def update(
        self,
        current_bid_volume: float,
        current_ask_volume: float
    ) -> OrderFlowMetrics:
        """
        Update the tracker with current volumes and compute OFI.

        Args:
            current_bid_volume: Current total bid volume in the order book
            current_ask_volume: Current total ask volume in the order book

        Returns:
            OrderFlowMetrics with delta volumes and OFI
        """
        if not self._is_initialized:
            # First update - initialize previous values
            self._prev_bid_volume = current_bid_volume
            self._prev_ask_volume = current_ask_volume
            self._is_initialized = True

            # Return zero deltas for first update
            return OrderFlowMetrics(
                delta_bid_volume=0.0,
                delta_ask_volume=0.0,
                ofi=0.0,
                cumulative_ofi=0.0
            )

        # Compute deltas
        delta_bid = current_bid_volume - self._prev_bid_volume
        delta_ask = current_ask_volume - self._prev_ask_volume

        # Compute OFI
        ofi = delta_bid - delta_ask

        # Update cumulative OFI
        self._cumulative_ofi += ofi

        # Store current values for next update
        self._prev_bid_volume = current_bid_volume
        self._prev_ask_volume = current_ask_volume

        return OrderFlowMetrics(
            delta_bid_volume=delta_bid,
            delta_ask_volume=delta_ask,
            ofi=ofi,
            cumulative_ofi=self._cumulative_ofi
        )

    @property
    def cumulative_ofi(self) -> float:
        """Get the current cumulative OFI."""
        return self._cumulative_ofi

    @property
    def is_initialized(self) -> bool:
        """Check if the tracker has been initialized with at least one update."""
        return self._is_initialized


class AggregatedOFITracker:
    """
    Tracks OFI with time-based aggregation.

    Useful for computing OFI over specific time windows (e.g., per second).
    """

    def __init__(self):
        """Initialize the aggregated OFI tracker."""
        self._base_tracker = OrderFlowTracker()
        self._aggregated_ofi: float = 0.0
        self._update_count: int = 0

    def update(
        self,
        current_bid_volume: float,
        current_ask_volume: float
    ) -> OrderFlowMetrics:
        """
        Update with current volumes.

        Args:
            current_bid_volume: Current total bid volume
            current_ask_volume: Current total ask volume

        Returns:
            OrderFlowMetrics for this update
        """
        metrics = self._base_tracker.update(current_bid_volume, current_ask_volume)

        # Accumulate OFI for aggregation
        self._aggregated_ofi += metrics.ofi
        self._update_count += 1

        return metrics

    def get_aggregated_ofi(self) -> float:
        """Get the aggregated OFI since last reset."""
        return self._aggregated_ofi

    def get_update_count(self) -> int:
        """Get the number of updates since last reset."""
        return self._update_count

    def reset_aggregation(self) -> float:
        """
        Reset the aggregation and return the final aggregated OFI.

        Returns:
            The aggregated OFI value before reset
        """
        final_ofi = self._aggregated_ofi
        self._aggregated_ofi = 0.0
        self._update_count = 0
        return final_ofi

    def reset(self):
        """Full reset of the tracker."""
        self._base_tracker.reset()
        self._aggregated_ofi = 0.0
        self._update_count = 0
