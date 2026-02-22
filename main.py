"""
Main orchestration module for the Order Book Liquidity Visualization Dashboard.

This module coordinates all layers:
- Data Ingestion (WebSocket connections)
- Order Book Reconstruction
- Feature Computation (Phase 2)
- Validation & Metrics Logging
- API Server (Phase 3)
- Dashboard (Phase 4+)
"""

import asyncio
import sys
import os
import signal
import time
import logging
import datetime
from typing import Dict
import threading
import psutil

# Create logs directory if it doesn't exist
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging to file and console
log_filename = os.path.join(log_dir, f"app_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# Remove any existing handlers
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# File Handler (DEBUG level)
file_handler = logging.FileHandler(log_filename)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
file_handler.setFormatter(file_formatter)
root_logger.addHandler(file_handler)

# Console Handler (INFO level)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from config import BINANCE_WEBSOCKET_BASE_URL, SYMBOLS, WEBSOCKET_UPDATE_INTERVAL
from orderbook.book_state import OrderBookState
from orderbook.sequence_validator import SequenceValidator

# Phase 2: Feature Engine imports
from features.basic_metrics import MetricsCalculator, OrderBookMetrics
from features.ofi import AggregatedOFITracker
from state.rolling_buffer import RollingBuffer
from state.level_history import LevelHistoryBuffer
from features.liquidity_detection import LiquidityDetector, DetectionEvent

# Import websocket connection function
import websockets
import json

# Phase 3: API Server imports
import uvicorn
from api.websocket_server import server as api_server

# Phase 4: Dashboard imports
from dashboard.app import app as dash_app, set_data_sources as dash_set_data_sources


class OrderBookOrchestrator:
    """
    Main orchestrator that coordinates all system components.
    Responsible for lifecycle management and validation.
    """

    def __init__(self):
        # Core components
        self.book_states: Dict[str, OrderBookState] = {}
        self.validators: Dict[str, SequenceValidator] = {}
        self.stop_event = asyncio.Event()

        # Phase 1: Update tracking
        self.update_counts: Dict[str, int] = {}
        self.last_update_times: Dict[str, float] = {}

        # Phase 2: Feature Engine components
        self.metrics_calculators: Dict[str, MetricsCalculator] = {}
        self.ofi_trackers: Dict[str, AggregatedOFITracker] = {}
        self.rolling_buffers: Dict[str, RollingBuffer] = {}

        # Phase 4: Level history for heatmap/surface charts
        self.level_histories: Dict[str, LevelHistoryBuffer] = {}

        # Phase 5: Liquidity detection
        self.detectors: Dict[str, LiquidityDetector] = {}
        self.detection_events: Dict[str, list] = {}

        # Phase 7: Monitoring & stability
        self.start_time: float = time.time()
        self.last_message_times: Dict[str, float] = {}
        self.latency_totals: Dict[str, float] = {}
        self.latency_counts: Dict[str, int] = {}
        self.latency_avgs: Dict[str, float] = {}
        self.low_rate_streak: Dict[str, int] = {}
        self.reconnect_counts: Dict[str, int] = {}
        self.memory_baseline_mb: float = 0.0
        self.current_memory_mb: float = 0.0
        self.process = psutil.Process(os.getpid())

    def initialize_symbols(self):
        """Initialize order book state, validators, and feature components for all symbols."""
        for symbol in SYMBOLS:
            # Phase 1: Core components
            book_state = OrderBookState(symbol)
            validator = SequenceValidator(symbol, book_state)
            self.book_states[symbol] = book_state
            self.validators[symbol] = validator
            self.update_counts[symbol] = 0
            self.last_update_times[symbol] = time.time()

            # Phase 2: Feature Engine components
            self.metrics_calculators[symbol] = MetricsCalculator(depth_levels=10)
            self.ofi_trackers[symbol] = AggregatedOFITracker()
            self.rolling_buffers[symbol] = RollingBuffer(window_seconds=180)

            # Phase 4: Level history buffer
            self.level_histories[symbol] = LevelHistoryBuffer(window_seconds=180)

            # Phase 5: Liquidity detector
            self.detectors[symbol] = LiquidityDetector()
            self.detection_events[symbol] = []

        logger.info(f"Initialized {len(SYMBOLS)} symbols with Feature Engine: {SYMBOLS}")

    async def connect_websocket(self, symbol: str):
        """Connect to Binance WebSocket for a specific symbol with auto-reconnect."""
        stream_url = f"{BINANCE_WEBSOCKET_BASE_URL}/{symbol.lower()}@depth@{WEBSOCKET_UPDATE_INTERVAL}"
        reconnect_delay = 1.0

        while not self.stop_event.is_set():
            logger.info(f"[{symbol}] Connecting to {stream_url}")
            try:
                async with websockets.connect(stream_url) as ws:
                    reconnect_delay = 1.0  # Reset on successful connect
                    while not self.stop_event.is_set():
                        try:
                            message_raw = await asyncio.wait_for(ws.recv(), timeout=1)
                            t0 = time.time()
                            message = json.loads(message_raw)

                            # Phase 7: Heartbeat — record last message time
                            self.last_message_times[symbol] = t0

                            # Pass to validator (handles order book updates)
                            if symbol in self.validators:
                                self.validators[symbol].handle_websocket_message(message)

                                # Track update count for frequency validation
                                self.update_counts[symbol] = self.update_counts.get(symbol, 0) + 1

                                # Phase 7: Latency — accumulate processing time
                                elapsed_ms = (time.time() - t0) * 1000
                                self.latency_totals[symbol] = self.latency_totals.get(symbol, 0.0) + elapsed_ms
                                self.latency_counts[symbol] = self.latency_counts.get(symbol, 0) + 1

                                # Phase 2: Update OFI tracker on each update
                                if self.validators[symbol].is_snapshot_received:
                                    book = self.book_states[symbol]
                                    self.ofi_trackers[symbol].update(
                                        book.total_bid_volume,
                                        book.total_ask_volume
                                    )

                        except asyncio.TimeoutError:
                            pass
                        except json.JSONDecodeError:
                            logger.warning(f"[{symbol}] Failed to decode JSON message")
                        except Exception as e:
                            logger.error(f"[{symbol}] Error processing message: {e}", exc_info=True)

            except websockets.exceptions.ConnectionClosedOK:
                logger.info(f"[{symbol}] WebSocket closed gracefully.")
            except Exception as e:
                logger.error(f"[{symbol}] WebSocket connection error: {e}", exc_info=True)

            # Phase 7: Reconnection tracking
            if not self.stop_event.is_set():
                self.reconnect_counts[symbol] = self.reconnect_counts.get(symbol, 0) + 1
                logger.warning(f"[{symbol}] [RECONNECT] Attempt #{self.reconnect_counts[symbol]}, retrying in {reconnect_delay:.0f}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)  # Exponential backoff, cap at 30s
            else:
                break

        logger.info(f"[{symbol}] WebSocket loop ended.")

    async def heartbeat_watchdog(self):
        """Phase 7: Monitor for stale WebSocket connections."""
        heartbeat_timeout = 5.0  # seconds without a message = stale
        logger.info("[HEARTBEAT] Watchdog started (timeout: 5s)")
        await asyncio.sleep(5)  # Wait for connections to establish

        while not self.stop_event.is_set():
            await asyncio.sleep(2.0)
            now = time.time()
            for symbol in SYMBOLS:
                last = self.last_message_times.get(symbol)
                if last is not None:
                    elapsed = now - last
                    if elapsed > heartbeat_timeout:
                        logger.warning(
                            f"[{symbol}] [HEARTBEAT] No message for {elapsed:.1f}s — connection may be stale"
                        )

    async def memory_monitor(self):
        """Phase 7: Periodic memory usage monitoring."""
        logger.info("[MEMORY] Monitor started (interval: 30s)")
        await asyncio.sleep(10)  # Let system stabilize

        while not self.stop_event.is_set():
            try:
                rss = self.process.memory_info().rss
                self.current_memory_mb = rss / (1024 * 1024)

                if self.memory_baseline_mb == 0.0:
                    self.memory_baseline_mb = self.current_memory_mb
                    logger.info(f"[MEMORY] Baseline: {self.current_memory_mb:.1f} MB")
                else:
                    growth = self.current_memory_mb - self.memory_baseline_mb
                    growth_pct = (growth / self.memory_baseline_mb) * 100
                    logger.info(f"[MEMORY] Current: {self.current_memory_mb:.1f} MB (baseline: {self.memory_baseline_mb:.1f} MB, growth: {growth:+.1f} MB / {growth_pct:+.0f}%)")

                    if growth_pct > 50:
                        logger.warning(f"[MEMORY] Growth exceeds 50% — possible memory leak")
            except Exception as e:
                logger.error(f"[MEMORY] Profiling error: {e}")

            await asyncio.sleep(30.0)

    async def feature_logger(self):
        """
        Feature Engine: Periodic computation and logging of all metrics.
        Runs every 1 second to compute metrics, update buffers, run detection,
        and log monitoring stats.
        """
        logger.info("Starting Feature Engine logger (1-second interval)...")
        await asyncio.sleep(2)  # Wait for initial sync

        while not self.stop_event.is_set():
            await asyncio.sleep(1.0)

            for symbol in SYMBOLS:
                book = self.book_states.get(symbol)
                validator = self.validators.get(symbol)

                if not validator or not validator.is_snapshot_received:
                    logger.debug(f"[{symbol}] Waiting for initial sync...")
                    continue

                # Phase 2: Compute all metrics
                metrics_calc = self.metrics_calculators[symbol]
                ofi_tracker = self.ofi_trackers[symbol]
                rolling_buffer = self.rolling_buffers[symbol]

                # Compute full metrics
                metrics = metrics_calc.compute_all_metrics(book)

                # Get aggregated OFI for this second
                aggregated_ofi = ofi_tracker.get_aggregated_ofi()
                ofi_update_count = ofi_tracker.get_update_count()
                ofi_tracker.reset_aggregation()  # Reset for next second

                # Update rolling buffer
                rolling_buffer.update(
                    mid_price=metrics.mid_price,
                    spread=metrics.spread,
                    imbalance=metrics.bid_ask_imbalance,
                    ofi=aggregated_ofi,
                    total_bid_volume=metrics.total_bid_volume,
                    total_ask_volume=metrics.total_ask_volume,
                    microprice=metrics.microprice
                )

                # Phase 4: Snapshot price levels for heatmap/surface
                self.level_histories[symbol].update(book)

                # Phase 5: Run liquidity detection
                events = self.detectors[symbol].update(
                    self.level_histories[symbol], rolling_buffer
                )
                self.detection_events[symbol] = events
                for evt in events:
                    if evt.is_new:
                        logger.info(f"[{symbol}] [DETECTION] {evt.details}")

                # Get update frequency
                updates = self.update_counts.get(symbol, 0)

                # Phase 7: Latency — compute per-second average
                lat_total = self.latency_totals.get(symbol, 0.0)
                lat_count = self.latency_counts.get(symbol, 0)
                avg_latency_ms = (lat_total / lat_count) if lat_count > 0 else 0.0
                self.latency_avgs[symbol] = avg_latency_ms
                self.latency_totals[symbol] = 0.0
                self.latency_counts[symbol] = 0

                # Log comprehensive metrics with latency
                logger.info(
                    f"[{symbol}] Mid: {metrics.mid_price:.2f} | "
                    f"Spread: {metrics.spread:.2f} | "
                    f"Imbal: {metrics.bid_ask_imbalance:+.3f} | "
                    f"OFI: {aggregated_ofi:+.4f} | "
                    f"Buffer: {rolling_buffer.size}/180 | "
                    f"Updates: {updates}/s | "
                    f"Latency: {avg_latency_ms:.2f}ms"
                )

                # Phase 7: Update rate monitoring
                if updates < 8:
                    self.low_rate_streak[symbol] = self.low_rate_streak.get(symbol, 0) + 1
                    if self.low_rate_streak[symbol] >= 3:
                        logger.warning(f"[{symbol}] [RATE] Low update rate for {self.low_rate_streak[symbol]}s ({updates}/s, expected ~10/s)")
                elif updates > 12:
                    self.low_rate_streak[symbol] = 0
                    logger.warning(f"[{symbol}] [RATE] High update rate: {updates}/s (expected ~10/s)")
                else:
                    self.low_rate_streak[symbol] = 0

                # Phase 7: Log reconnection count if any
                recon = self.reconnect_counts.get(symbol, 0)
                if recon > 0:
                    logger.info(f"[{symbol}] [RECONNECT] Total reconnections: {recon}")

                # Validation checks
                if metrics.spread <= 0:
                    logger.warning(f"[{symbol}] VALIDATION FAIL: Spread not positive! {metrics.spread}")

                if metrics.best_bid >= metrics.best_ask:
                    logger.warning(f"[{symbol}] VALIDATION FAIL: Bids >= Asks!")

                # Phase 2 validation: Check for NaN values
                if any([
                    metrics.mid_price != metrics.mid_price,  # NaN check
                    metrics.microprice != metrics.microprice,
                    metrics.bid_ask_imbalance != metrics.bid_ask_imbalance
                ]):
                    logger.warning(f"[{symbol}] VALIDATION FAIL: NaN values detected!")

                # Reset counter
                self.update_counts[symbol] = 0

        logger.info("Feature Engine logger stopped.")

    def _run_dash_server(self, host: str = "127.0.0.1", port: int = 8050):
        """Run the Dash dashboard server in a separate thread."""
        import logging as _logging
        # Suppress werkzeug request logging noise
        _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
        dash_app.run(host=host, port=port, debug=False, use_reloader=False)

    def _run_api_server(self, host: str = "127.0.0.1", port: int = 8000):
        """Run the FastAPI server in a separate thread."""
        config = uvicorn.Config(
            api_server.app,
            host=host,
            port=port,
            log_level="warning",  # Reduce uvicorn logging noise
            access_log=False
        )
        server = uvicorn.Server(config)
        # Run in thread - this blocks until shutdown
        asyncio.run(server.serve())

    async def run(self):
        """Main run loop - orchestrates all components."""
        logger.info("=" * 60)
        logger.info("Order Book Liquidity Visualization Dashboard")
        logger.info("=" * 60)
        self.start_time = time.time()

        # Initialize
        self.initialize_symbols()

        # Phase 3: Configure API server with data sources
        api_server.set_data_sources(
            book_states=self.book_states,
            rolling_buffers=self.rolling_buffers,
            ofi_trackers=self.ofi_trackers,
            metrics_calculators=self.metrics_calculators,
            valid_symbols=SYMBOLS
        )

        # Phase 7: Pass monitoring references to API server
        api_server.set_monitoring_refs(self)

        # Start API server in background thread
        api_thread = threading.Thread(
            target=self._run_api_server,
            kwargs={"host": "127.0.0.1", "port": 8000},
            daemon=True
        )
        api_thread.start()
        logger.info("[API] FastAPI server started on ws://127.0.0.1:8000/ws/{symbol}")

        # Phase 4: Configure and start Dash dashboard
        dash_set_data_sources(
            book_states=self.book_states,
            rolling_buffers=self.rolling_buffers,
            level_histories=self.level_histories,
            metrics_calculators=self.metrics_calculators,
            symbols=SYMBOLS,
            detection_events=self.detection_events,
        )

        dash_thread = threading.Thread(
            target=self._run_dash_server,
            kwargs={"host": "127.0.0.1", "port": 8050},
            daemon=True
        )
        dash_thread.start()
        logger.info("[Dashboard] Dash server started on http://127.0.0.1:8050")

        # Start WebSocket connections
        websocket_tasks = []
        for symbol in SYMBOLS:
            task = asyncio.create_task(self.connect_websocket(symbol))
            websocket_tasks.append(task)

        # Brief delay for WebSocket to establish
        await asyncio.sleep(0.5)

        # Start synchronization for each symbol
        for symbol in SYMBOLS:
            asyncio.create_task(self.validators[symbol].start_synchronization())

        # Start feature engine logger
        feature_task = asyncio.create_task(self.feature_logger())

        # Phase 7: Start monitoring coroutines
        heartbeat_task = asyncio.create_task(self.heartbeat_watchdog())
        memory_task = asyncio.create_task(self.memory_monitor())

        # Phase 3: Start API push loop
        await api_server.start_push_loop()

        try:
            # Wait for tasks
            await asyncio.gather(*websocket_tasks, return_exceptions=True)
        except KeyboardInterrupt:
            logger.info("\nShutdown requested...")
            self.stop_event.set()
        finally:
            self.stop_event.set()

            # Stop API push loop
            await api_server.stop_push_loop()

            feature_task.cancel()
            heartbeat_task.cancel()
            memory_task.cancel()

            for task in websocket_tasks:
                task.cancel()
            await asyncio.gather(
                *websocket_tasks, feature_task, heartbeat_task, memory_task,
                return_exceptions=True
            )

            # Flush rolling buffers
            for symbol in SYMBOLS:
                self.rolling_buffers[symbol].flush()

            # Final state with Phase 4 metrics
            logger.info("\n" + "=" * 60)
            logger.info("Final State (Phase 4 Metrics):")
            logger.info("=" * 60)
            for symbol in SYMBOLS:
                book = self.book_states[symbol]
                metrics = self.metrics_calculators[symbol].compute_all_metrics(book)
                buffer = self.rolling_buffers[symbol]

                logger.info(f"[{symbol}]")
                logger.info(f"  Mid: {metrics.mid_price:.2f} | Microprice: {metrics.microprice:.2f}")
                logger.info(f"  Spread: {metrics.spread:.2f} | Imbalance: {metrics.bid_ask_imbalance:+.3f}")
                logger.info(f"  Rolling Buffer: {buffer.size} data points")


def main():
    """Entry point."""
    orchestrator = OrderBookOrchestrator()

    # Handle Windows Ctrl+C gracefully
    if sys.platform == "win32":
        # Windows-specific signal handling
        signal.signal(signal.SIGINT, lambda s, f: orchestrator.stop_event.set())

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
