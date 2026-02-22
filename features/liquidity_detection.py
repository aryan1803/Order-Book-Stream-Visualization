"""
Liquidity Detection Module (Phase 5)

Implements three order-book-based microstructure detectors:
1. Liquidity Wall Detection — large persistent orders at a price level
2. Layering Detection — suspicious adjacent abnormal levels that change frequently
3. Liquidity Exhaustion — thinning depth + widening spread before breakout

All detectors work purely from order book depth data (no trade data required).
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from statistics import median

logger = logging.getLogger(__name__)


@dataclass
class DetectionEvent:
    """A single detection event."""
    event_type: str   # "WALL", "LAYERING", "EXHAUSTION"
    side: str         # "BID", "ASK", "BOTH"
    price: Optional[float] = None
    size: Optional[float] = None
    duration: float = 0.0  # seconds (for walls)
    details: str = ""
    timestamp: float = field(default_factory=time.time)
    is_new: bool = True  # False for ongoing walls (already reported)


class LiquidityDetector:
    """
    Runs detection algorithms against order book state every 1 second.

    Usage:
        detector = LiquidityDetector()
        events = detector.update(level_history, rolling_buffer)
    """

    # Wall detection thresholds
    WALL_MULTIPLIER = 5.0       # level_size > N × median
    WALL_PERSIST_SECONDS = 5.0  # must persist this long
    WALL_MEDIAN_WINDOW = 30     # seconds of history for median

    # Layering detection thresholds
    LAYERING_MULTIPLIER = 8.0   # level_size > N × median for "abnormal"
    LAYERING_MIN_ADJACENT = 4   # minimum adjacent abnormal levels
    LAYERING_CHURN_THRESHOLD = 0.6  # 60%+ of abnormal levels must be newly appeared
    LAYERING_COOLDOWN = 30.0    # seconds between alerts per side

    # Exhaustion detection thresholds
    EXHAUSTION_DEPTH_RATIO = 0.5    # current depth < 50% of avg
    EXHAUSTION_SPREAD_RATIO = 2.0   # current spread > 2× avg
    EXHAUSTION_WINDOW = 15          # seconds of history

    def __init__(self):
        # Wall tracking: (side, price) -> first_seen_timestamp
        self._wall_tracker: Dict[Tuple[str, float], float] = {}
        # Walls already reported (to avoid repeated logging)
        self._reported_walls: set = set()

        # Previous snapshot for layering change detection
        self._prev_bid_sizes: Dict[float, float] = {}
        self._prev_ask_sizes: Dict[float, float] = {}

        # Previous abnormal price sets for layering churn detection
        self._prev_abnormal_bids: set = set()
        self._prev_abnormal_asks: set = set()

        # Layering cooldown tracking: side -> last_fired_timestamp
        self._layering_last_fired: Dict[str, float] = {}

    def update(self, level_history, rolling_buffer) -> List[DetectionEvent]:
        """
        Run all detectors and return active events.

        Args:
            level_history: LevelHistoryBuffer with 180s of snapshots
            rolling_buffer: RollingBuffer with aggregated metrics

        Returns:
            List of currently active DetectionEvent instances.
        """
        events = []

        snapshots = list(level_history._buffer)
        if len(snapshots) < 2:
            return events

        current = snapshots[-1]

        # 1. Wall detection
        wall_events = self._detect_walls(snapshots)
        events.extend(wall_events)

        # 2. Layering detection
        layering_events = self._detect_layering(current, snapshots)
        events.extend(layering_events)

        # 3. Exhaustion detection
        exhaustion_events = self._detect_exhaustion(rolling_buffer)
        events.extend(exhaustion_events)

        # Update previous snapshot for next iteration
        self._prev_bid_sizes = {p: s for p, s in current.bid_levels}
        self._prev_ask_sizes = {p: s for p, s in current.ask_levels}

        return events

    def _compute_median_size(self, snapshots, window: int) -> float:
        """Compute median level size from the last N snapshots."""
        recent = snapshots[-window:] if len(snapshots) >= window else snapshots
        all_sizes = []
        for snap in recent:
            for _, size in snap.bid_levels:
                all_sizes.append(size)
            for _, size in snap.ask_levels:
                all_sizes.append(size)

        if not all_sizes:
            return 0.0
        return median(all_sizes)

    def _detect_walls(self, snapshots) -> List[DetectionEvent]:
        """
        Detect liquidity walls: levels with size > 3× rolling median
        that persist for > 5 seconds.
        """
        events = []
        now = time.time()
        median_size = self._compute_median_size(snapshots, self.WALL_MEDIAN_WINDOW)

        if median_size <= 0:
            return events

        threshold = self.WALL_MULTIPLIER * median_size
        current = snapshots[-1]

        # Collect all current large levels
        current_large = set()

        for price, size in current.bid_levels:
            if size > threshold:
                key = ("BID", price)
                current_large.add(key)
                if key not in self._wall_tracker:
                    self._wall_tracker[key] = now

        for price, size in current.ask_levels:
            if size > threshold:
                key = ("ASK", price)
                current_large.add(key)
                if key not in self._wall_tracker:
                    self._wall_tracker[key] = now

        # Prune entries no longer present
        stale_keys = [k for k in self._wall_tracker if k not in current_large]
        for k in stale_keys:
            del self._wall_tracker[k]
            self._reported_walls.discard(k)

        # Flag entries persisting > threshold duration
        for (side, price), first_seen in self._wall_tracker.items():
            duration = now - first_seen
            key = (side, price)
            if duration >= self.WALL_PERSIST_SECONDS:
                newly_detected = key not in self._reported_walls
                if newly_detected:
                    self._reported_walls.add(key)

                # Find the current size
                levels = current.bid_levels if side == "BID" else current.ask_levels
                size = next((s for p, s in levels if p == price), 0.0)

                events.append(DetectionEvent(
                    event_type="WALL",
                    side=side,
                    price=price,
                    size=size,
                    duration=round(duration, 1),
                    details=f"{side} wall at {price:,.2f}: {size:.3f} ({duration:.0f}s, {size/median_size:.1f}× median)",
                    is_new=newly_detected,
                ))

        return events

    def _detect_layering(self, current, snapshots) -> List[DetectionEvent]:
        """
        Detect layering: 3+ adjacent levels with abnormally large size
        where the set of abnormal prices is churning (appearing/disappearing).

        This catches spoofing-style behavior where large orders are rapidly
        placed and cancelled at different price levels.
        """
        events = []
        now = time.time()
        median_size = self._compute_median_size(snapshots, self.WALL_MEDIAN_WINDOW)

        if median_size <= 0:
            return events

        threshold = self.LAYERING_MULTIPLIER * median_size

        for side, levels, prev_abnormal_attr in [
            ("BID", current.bid_levels, "_prev_abnormal_bids"),
            ("ASK", current.ask_levels, "_prev_abnormal_asks"),
        ]:
            if len(levels) < self.LAYERING_MIN_ADJACENT:
                continue

            # Identify abnormal levels (prices with extreme size)
            current_abnormal = {price for price, size in levels if size > threshold}

            # Always update previous abnormal set (even during cooldown)
            prev_abnormal = getattr(self, prev_abnormal_attr)
            setattr(self, prev_abnormal_attr, current_abnormal)

            # Cooldown check — skip alerting but still track state
            last_fired = self._layering_last_fired.get(side, 0.0)
            if now - last_fired < self.LAYERING_COOLDOWN:
                continue

            # Need previous data to compute churn
            if not prev_abnormal:
                continue

            # Find runs of adjacent abnormal levels
            abnormal_flags = [size > threshold for _, size in levels]
            max_run = 0
            run = 0
            for flag in abnormal_flags:
                if flag:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0

            if max_run < self.LAYERING_MIN_ADJACENT:
                continue

            # Check churn: how many abnormal prices are NEW vs previous snapshot
            newly_appeared = current_abnormal - prev_abnormal
            churn_ratio = len(newly_appeared) / len(current_abnormal) if current_abnormal else 0

            if churn_ratio >= self.LAYERING_CHURN_THRESHOLD:
                self._layering_last_fired[side] = now
                events.append(DetectionEvent(
                    event_type="LAYERING",
                    side=side,
                    details=(
                        f"{side} layering: {max_run} adjacent abnormal levels "
                        f"({len(current_abnormal)} total, {len(newly_appeared)} new, "
                        f"{churn_ratio:.0%} churn)"
                    ),
                ))

        return events

    def _detect_exhaustion(self, rolling_buffer) -> List[DetectionEvent]:
        """
        Detect liquidity exhaustion: rapid thinning of depth
        combined with widening spread.
        """
        events = []

        window_data = rolling_buffer.get_window(self.EXHAUSTION_WINDOW)
        if len(window_data) < 5:
            return events

        # Compute averages over the window
        depths = [s.total_bid_volume + s.total_ask_volume for s in window_data]
        spreads = [s.spread for s in window_data]

        avg_depth = sum(depths) / len(depths)
        avg_spread = sum(spreads) / len(spreads)

        if avg_depth <= 0 or avg_spread <= 0:
            return events

        # Current values (latest in window)
        current_depth = depths[-1]
        current_spread = spreads[-1]

        depth_ratio = current_depth / avg_depth
        spread_ratio = current_spread / avg_spread

        if (depth_ratio < self.EXHAUSTION_DEPTH_RATIO and
                spread_ratio > self.EXHAUSTION_SPREAD_RATIO):
            events.append(DetectionEvent(
                event_type="EXHAUSTION",
                side="BOTH",
                details=(
                    f"Liquidity exhaustion: depth at {depth_ratio:.0%} of avg "
                    f"({current_depth:.2f} vs {avg_depth:.2f}), "
                    f"spread at {spread_ratio:.1f}× avg "
                    f"({current_spread:.4f} vs {avg_spread:.4f})"
                ),
            ))

        return events
