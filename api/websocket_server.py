"""
FastAPI WebSocket Server Module (Phase 3.1-3.3)

Provides real-time order book data to dashboard clients via WebSocket.

Endpoint: /ws/{symbol}
Push frequency: 1 second
Payload: JSON with mid_price, spread, imbalance, ofi, bid_levels, ask_levels

Phase 3.3: Burst Protection
- Uses asyncio.Queue per client to buffer messages
- On overflow: drops intermediate messages, keeps most recent
- Logs burst events for monitoring
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from features.market_impact import simulate_market_order

logger = logging.getLogger(__name__)

# Burst protection settings
MAX_QUEUE_SIZE = 5  # Maximum messages to buffer per client
BURST_LOG_INTERVAL = 10  # Log burst events every N occurrences


@dataclass
class ClientConnection:
    """Represents a connected WebSocket client with burst protection."""
    websocket: WebSocket
    symbol: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE))
    sender_task: Optional[asyncio.Task] = None
    burst_count: int = 0  # Count of dropped messages due to overflow
    total_burst_count: int = 0  # Total dropped messages over connection lifetime
    last_burst_log_time: float = 0.0
    connected_at: float = field(default_factory=time.time)


class ConnectionManager:
    """
    Manages WebSocket connections for multiple symbols with burst protection.

    Handles:
    - Client connection/disconnection
    - Message queuing with overflow protection
    - Broadcasting messages to all clients subscribed to a symbol
    - Connection tracking per symbol
    """

    def __init__(self):
        # Map of symbol -> set of ClientConnection objects
        self._connections: Dict[str, Set[ClientConnection]] = {}
        # Map of WebSocket -> ClientConnection for quick lookup
        self._client_map: Dict[WebSocket, ClientConnection] = {}
        self._lock = asyncio.Lock()

    async def _sender_loop(self, client: ClientConnection):
        """
        Background task that drains the message queue for a client.
        Handles sending messages and detecting disconnections.
        """
        try:
            while True:
                # Wait for a message in the queue
                message_json = await client.queue.get()
                try:
                    await client.websocket.send_text(message_json)
                except Exception as e:
                    logger.debug(f"[API] Send failed for {client.symbol} client: {e}")
                    break
                finally:
                    client.queue.task_done()
        except asyncio.CancelledError:
            pass

    async def connect(self, websocket: WebSocket, symbol: str) -> ClientConnection:
        """Accept a new WebSocket connection for a symbol."""
        await websocket.accept()

        client = ClientConnection(websocket=websocket, symbol=symbol)

        async with self._lock:
            if symbol not in self._connections:
                self._connections[symbol] = set()
            self._connections[symbol].add(client)
            self._client_map[websocket] = client

        # Start the sender task for this client
        client.sender_task = asyncio.create_task(self._sender_loop(client))

        logger.info(f"[API] Client connected to {symbol}. Total clients: {len(self._connections.get(symbol, set()))}")
        return client

    async def disconnect(self, websocket: WebSocket, symbol: str):
        """Remove a WebSocket connection and clean up resources."""
        async with self._lock:
            client = self._client_map.pop(websocket, None)
            if client:
                if symbol in self._connections:
                    self._connections[symbol].discard(client)
                    if not self._connections[symbol]:
                        del self._connections[symbol]

                # Cancel the sender task
                if client.sender_task:
                    client.sender_task.cancel()
                    try:
                        await client.sender_task
                    except asyncio.CancelledError:
                        pass

                # Log final burst stats if any drops occurred
                if client.total_burst_count > 0:
                    logger.info(
                        f"[API] Client disconnected from {symbol}. "
                        f"Total messages dropped due to burst: {client.total_burst_count}"
                    )
                else:
                    logger.info(f"[API] Client disconnected from {symbol}")

    async def queue_message(self, symbol: str, message: dict):
        """
        Queue a message for all clients subscribed to a symbol.

        Implements burst protection:
        - If queue is full, drops the oldest message and adds the new one
        - Logs burst events periodically
        """
        if symbol not in self._connections:
            return

        message_json = json.dumps(message)

        async with self._lock:
            clients = list(self._connections.get(symbol, set()))

        for client in clients:
            try:
                # Try to add to queue without blocking
                try:
                    client.queue.put_nowait(message_json)
                except asyncio.QueueFull:
                    # Queue is full - implement burst protection
                    # Drop the oldest message and add the new one
                    try:
                        # Remove oldest (this is a blocking get but queue is full so instant)
                        client.queue.get_nowait()
                        client.queue.task_done()
                        # Add new message
                        client.queue.put_nowait(message_json)

                        # Track burst event
                        client.burst_count += 1
                        client.total_burst_count += 1

                        # Log burst events periodically
                        current_time = time.time()
                        if client.burst_count >= BURST_LOG_INTERVAL or \
                           (current_time - client.last_burst_log_time) > 60:
                            logger.warning(
                                f"[API] BURST: Dropped {client.burst_count} messages for {symbol} client "
                                f"(total: {client.total_burst_count})"
                            )
                            client.burst_count = 0
                            client.last_burst_log_time = current_time

                    except asyncio.QueueEmpty:
                        # Shouldn't happen, but handle gracefully
                        pass

            except Exception as e:
                logger.error(f"[API] Error queuing message for {symbol}: {e}")

    def get_connection_count(self, symbol: str) -> int:
        """Get the number of active connections for a symbol."""
        return len(self._connections.get(symbol, set()))

    def get_total_connections(self) -> int:
        """Get total number of active connections across all symbols."""
        return sum(len(conns) for conns in self._connections.values())

    def get_burst_stats(self) -> Dict[str, Any]:
        """Get burst protection statistics."""
        stats = {
            "total_connections": self.get_total_connections(),
            "symbols": {}
        }
        for symbol, clients in self._connections.items():
            symbol_stats = {
                "connections": len(clients),
                "total_burst_drops": sum(c.total_burst_count for c in clients),
                "clients_with_bursts": sum(1 for c in clients if c.total_burst_count > 0)
            }
            stats["symbols"][symbol] = symbol_stats
        return stats


class WebSocketServer:
    """
    FastAPI WebSocket server for streaming order book data.

    Usage:
        server = WebSocketServer()
        server.set_data_sources(book_states, rolling_buffers, ofi_trackers)
        # Run with uvicorn or integrate with main.py
    """

    def __init__(self):
        self.app = FastAPI(title="Order Book WebSocket API")
        self.manager = ConnectionManager()

        # Data sources (set by orchestrator)
        self._book_states: Dict[str, Any] = {}
        self._rolling_buffers: Dict[str, Any] = {}
        self._ofi_trackers: Dict[str, Any] = {}
        self._metrics_calculators: Dict[str, Any] = {}

        # Valid symbols
        self._valid_symbols: Set[str] = set()

        # Phase 7: Monitoring reference (set by orchestrator)
        self._orchestrator: Optional[Any] = None

        # Push task
        self._push_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # Setup CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Configure FastAPI routes."""

        @self.app.websocket("/ws/{symbol}")
        async def websocket_endpoint(websocket: WebSocket, symbol: str):
            """WebSocket endpoint for streaming order book data."""
            symbol = symbol.lower()

            if symbol not in self._valid_symbols:
                await websocket.close(code=4000, reason=f"Invalid symbol: {symbol}")
                return

            client = await self.manager.connect(websocket, symbol)

            try:
                # Keep connection alive, listen for client messages (ping/pong)
                while True:
                    try:
                        # Wait for any message from client (keeps connection alive)
                        data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                        # Client can send "ping" to keep alive
                        if data == "ping":
                            await websocket.send_text("pong")
                    except asyncio.TimeoutError:
                        # Send a ping to check if client is still alive
                        try:
                            await websocket.send_text(json.dumps({"type": "ping"}))
                        except Exception:
                            break
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.error(f"[API] WebSocket error for {symbol}: {e}")
            finally:
                await self.manager.disconnect(websocket, symbol)

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint with monitoring stats."""
            response = {
                "status": "healthy",
                "connections": self.manager.get_total_connections(),
                "symbols": list(self._valid_symbols),
            }

            # Phase 7: Add monitoring stats if available
            orch = self._orchestrator
            if orch is not None:
                uptime = time.time() - orch.start_time
                response["uptime_seconds"] = round(uptime, 1)
                response["memory_mb"] = round(orch.current_memory_mb, 1)

                per_symbol = {}
                for sym in self._valid_symbols:
                    per_symbol[sym] = {
                        "latency_avg_ms": round(orch.latency_avgs.get(sym, 0.0), 2),
                        "reconnect_count": orch.reconnect_counts.get(sym, 0),
                    }
                response["monitoring"] = per_symbol

            return response

        @self.app.get("/symbols")
        async def get_symbols():
            """Get list of available symbols."""
            return {"symbols": list(self._valid_symbols)}

        @self.app.get("/stats")
        async def get_stats():
            """Get server statistics including burst protection stats."""
            return {
                "burst_protection": self.manager.get_burst_stats(),
                "queue_size": MAX_QUEUE_SIZE
            }

        @self.app.get("/simulate/{symbol}")
        async def simulate_impact(
            symbol: str,
            side: str = Query("buy", pattern="^(buy|sell)$"),
            size: float = Query(..., gt=0),
        ):
            """Simulate market order impact against current order book."""
            symbol = symbol.lower()

            if symbol not in self._valid_symbols:
                return {"error": f"Invalid symbol: {symbol}"}

            book = self._book_states.get(symbol)
            if not book or book.best_bid == 0:
                return {"error": f"Order book not ready for {symbol}"}

            return simulate_market_order(book, side, size)

    def set_data_sources(
        self,
        book_states: Dict[str, Any],
        rolling_buffers: Dict[str, Any],
        ofi_trackers: Dict[str, Any],
        metrics_calculators: Dict[str, Any],
        valid_symbols: List[str]
    ):
        """
        Set the data sources from the orchestrator.

        Args:
            book_states: Dict of symbol -> OrderBookState
            rolling_buffers: Dict of symbol -> RollingBuffer
            ofi_trackers: Dict of symbol -> AggregatedOFITracker
            metrics_calculators: Dict of symbol -> MetricsCalculator
            valid_symbols: List of valid symbol names
        """
        self._book_states = book_states
        self._rolling_buffers = rolling_buffers
        self._ofi_trackers = ofi_trackers
        self._metrics_calculators = metrics_calculators
        self._valid_symbols = set(s.lower() for s in valid_symbols)

        logger.info(f"[API] Data sources configured for symbols: {valid_symbols}")

    def set_monitoring_refs(self, orchestrator):
        """Phase 7: Set reference to orchestrator for monitoring stats."""
        self._orchestrator = orchestrator

    def _build_snapshot(self, symbol: str) -> Optional[dict]:
        """
        Build the JSON snapshot for a symbol.

        Returns:
            Dict with mid_price, spread, imbalance, ofi, bid_levels, ask_levels
        """
        book = self._book_states.get(symbol)
        buffer = self._rolling_buffers.get(symbol)
        ofi_tracker = self._ofi_trackers.get(symbol)
        metrics_calc = self._metrics_calculators.get(symbol)

        if not book or not metrics_calc:
            return None

        # Compute current metrics
        metrics = metrics_calc.compute_all_metrics(book)

        # Get latest OFI from buffer or tracker
        latest_ofi = 0.0
        if buffer:
            latest = buffer.get_latest()
            if latest:
                latest_ofi = latest.ofi

        # Build bid/ask levels (top 10)
        # Bids: highest price first (descending)
        bid_levels = [
            [float(price), float(qty)]
            for price, qty in reversed(list(book.bids.items())[-10:])
        ]

        # Asks: lowest price first (ascending)
        ask_levels = [
            [float(price), float(qty)]
            for price, qty in list(book.asks.items())[:10]
        ]

        return {
            "symbol": symbol,
            "mid_price": round(metrics.mid_price, 2),
            "spread": round(metrics.spread, 4),
            "imbalance": round(metrics.bid_ask_imbalance, 4),
            "ofi": round(latest_ofi, 4),
            "microprice": round(metrics.microprice, 2),
            "total_bid_volume": round(metrics.total_bid_volume, 4),
            "total_ask_volume": round(metrics.total_ask_volume, 4),
            "bid_levels": bid_levels,
            "ask_levels": ask_levels,
            "timestamp": time.time()
        }

    async def _push_loop(self):
        """
        Main push loop - queues snapshots for all connected clients every 1 second.
        Uses burst protection to handle slow clients.
        """
        logger.info("[API] Starting WebSocket push loop (1-second interval)...")

        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)

            for symbol in self._valid_symbols:
                if self.manager.get_connection_count(symbol) == 0:
                    continue

                snapshot = self._build_snapshot(symbol)
                if snapshot:
                    await self.manager.queue_message(symbol, snapshot)

        logger.info("[API] WebSocket push loop stopped.")

    async def start_push_loop(self):
        """Start the background push loop."""
        if self._push_task is None or self._push_task.done():
            self._stop_event.clear()
            self._push_task = asyncio.create_task(self._push_loop())

    async def stop_push_loop(self):
        """Stop the background push loop."""
        self._stop_event.set()
        if self._push_task:
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass


# Create global server instance for import
server = WebSocketServer()
app = server.app
