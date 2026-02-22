import asyncio
import collections
from typing import Any, Dict, Deque, Optional
import time 

import sys
import os
import logging

# Configure logging
logger = logging.getLogger(__name__)


# Add the project root to the sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import BINANCE_WEBSOCKET_BASE_URL, SYMBOLS, WEBSOCKET_UPDATE_INTERVAL
from data_ingestion.snapshot_loader import fetch_depth_snapshot
from orderbook.book_state import OrderBookState


class SequenceValidator:
    def __init__(self, symbol: str, book_state: OrderBookState):
        self.symbol = symbol
        self.book_state = book_state
        self.diff_buffer: Deque[Dict] = collections.deque()
        self.is_snapshot_received = False
        self.is_synchronizing = False  # Renamed from is_resyncing for clarity
        self.last_fetched_snapshot_id: int = -1  # lastUpdateId from the REST snapshot
        self.snapshot_fetch_lock = asyncio.Lock()
        self.MAX_SNAPSHOT_ATTEMPTS = 5  # Max attempts to get a valid (non-stale) snapshot
        self.initial_reconnect_delay = 0.5  # seconds between snapshot fetch retries
        self._critical_failure = False  # Flag to indicate unrecoverable state
        self._first_message_received = asyncio.Event()  # Signal when first WS message arrives
        self._last_processed_u: int = -1  # Track last applied event's 'u' for pu validation (Futures)
        self._awaiting_first_diff: bool = False  # True when waiting for first diff after snapshot

    async def _fetch_snapshot(self) -> Optional[Dict]:
        """
        Fetches a depth snapshot from REST API.
        Returns the snapshot dict or None on failure.
        """
        try:
            start_time = time.monotonic()
            snapshot = await fetch_depth_snapshot(self.symbol)
            fetch_duration = time.monotonic() - start_time
            logger.debug(f"[{self.symbol}] Snapshot fetch completed in {fetch_duration:.4f} seconds.")
            return snapshot
        except Exception as e:
            logger.error(f"[{self.symbol}] Exception during snapshot fetch: {e}", exc_info=True)
            return None

    def _get_first_usable_event_U(self) -> Optional[int]:
        """
        Returns the U value of the first event in buffer, or None if buffer is empty.
        """
        if self.diff_buffer:
            return self.diff_buffer[0]['U']
        return None

    async def start_synchronization(self):
        """
        Implements Binance's order book synchronization protocol:
        1. Wait for WebSocket to buffer at least one message
        2. Fetch snapshot
        3. If snapshot.lastUpdateId < first_buffered_U, re-fetch (snapshot is stale)
        4. Drop buffered events where u <= lastUpdateId
        5. Verify first remaining event has lastUpdateId in [U, u] range
        6. Apply diffs
        """
        if self._critical_failure:
            logger.debug(f"[{self.symbol}] Critical failure flag set, not starting synchronization.")
            return

        async with self.snapshot_fetch_lock:
            if self.is_snapshot_received or self.is_synchronizing:
                logger.debug(f"[{self.symbol}] Already synchronized or synchronizing, skipping.")
                return

            self.is_synchronizing = True
            logger.info(f"[{self.symbol}] Starting synchronization - waiting for first WebSocket message...")

            # Step 1: Wait for at least one WebSocket message to be buffered
            # This ensures we have a reference point (U of first event)
            try:
                await asyncio.wait_for(self._first_message_received.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.error(f"[{self.symbol}] Timeout waiting for first WebSocket message.")
                self.is_synchronizing = False
                return

            logger.debug(f"[{self.symbol}] First WebSocket message received, buffer size: {len(self.diff_buffer)}")

            # Steps 2-4: Fetch snapshot and verify it's not stale (loop until valid)
            for attempt in range(self.MAX_SNAPSHOT_ATTEMPTS):
                if attempt > 0:
                    delay = self.initial_reconnect_delay * (2 ** (attempt - 1))
                    logger.info(f"[{self.symbol}] Fetching new snapshot in {delay:.2f}s (attempt {attempt + 1}/{self.MAX_SNAPSHOT_ATTEMPTS})...")
                    await asyncio.sleep(delay)

                snapshot = await self._fetch_snapshot()
                if not snapshot:
                    logger.warning(f"[{self.symbol}] Snapshot fetch failed (attempt {attempt + 1}).")
                    continue

                snapshot_last_update_id = snapshot['lastUpdateId']

                # Binance Step 4: Check if snapshot is stale
                # If lastUpdateId < U of first buffered event, snapshot is too old
                first_buffered_U = self._get_first_usable_event_U()

                if first_buffered_U is None:
                    # Buffer became empty (shouldn't happen, but handle it)
                    logger.warning(f"[{self.symbol}] Buffer empty after snapshot fetch, waiting for more messages...")
                    await asyncio.sleep(0.2)  # Brief wait for more messages
                    first_buffered_U = self._get_first_usable_event_U()
                    if first_buffered_U is None:
                        continue

                logger.debug(f"[{self.symbol}] Snapshot lastUpdateId={snapshot_last_update_id}, first buffered U={first_buffered_U}")

                # Binance Step 4: If snapshot is strictly older than first buffered event, re-fetch
                if snapshot_last_update_id < first_buffered_U:
                    logger.info(f"[{self.symbol}] Snapshot stale (lastUpdateId {snapshot_last_update_id} < first U {first_buffered_U}). Fetching newer snapshot...")
                    continue  # Go back to step 3 (fetch another snapshot)

                # Snapshot is valid - initialize order book
                self.book_state.initialize_with_snapshot(snapshot)
                self.last_fetched_snapshot_id = snapshot_last_update_id
                logger.info(f"[{self.symbol}] Snapshot initialized with lastUpdateId: {self.last_fetched_snapshot_id}")

                # Step 5: Drop outdated buffered events (u <= lastUpdateId)
                self._drop_outdated_diffs()

                # Step 6: Verify and apply first diff
                if self._validate_and_apply_first_diff():
                    self.is_snapshot_received = True
                    self.is_synchronizing = False
                    logger.info(f"[{self.symbol}] Synchronization complete. Processing sequential diffs...")
                    self._process_sequential_diffs()
                    return
                else:
                    # First diff validation failed - need a newer snapshot
                    logger.warning(f"[{self.symbol}] First diff validation failed, fetching newer snapshot...")
                    continue

            # Exhausted all attempts
            logger.critical(f"[{self.symbol}] Failed to synchronize after {self.MAX_SNAPSHOT_ATTEMPTS} attempts.")
            self._critical_failure = True
            self.is_synchronizing = False
    
    def _drop_outdated_diffs(self):
        """
        Binance Step 5: Drop buffered events where u <= lastUpdateId of snapshot.
        """
        dropped_count = 0
        while self.diff_buffer and self.diff_buffer[0]['u'] <= self.last_fetched_snapshot_id:
            dropped_diff = self.diff_buffer.popleft()
            dropped_count += 1
            logger.debug(f"[{self.symbol}] Dropped outdated diff (u={dropped_diff['u']}) <= snapshot lastUpdateId ({self.last_fetched_snapshot_id})")

        if dropped_count > 0:
            logger.debug(f"[{self.symbol}] Dropped {dropped_count} outdated diffs. Remaining buffer size: {len(self.diff_buffer)}")

    def _validate_and_apply_first_diff(self) -> bool:
        """
        Binance Step 5 continued: The first buffered event should have lastUpdateId within [U, u] range.
        For Futures: the first diff uses U/u range validation (NOT pu), subsequent diffs use pu.
        Returns True if validation passed and diff was applied, False otherwise.
        """
        if not self.diff_buffer:
            logger.debug(f"[{self.symbol}] Buffer empty after dropping outdated diffs - waiting for new events.")
            # Flag that we're waiting for the first diff (use U/u validation, not pu)
            self._awaiting_first_diff = True
            return True  # No diffs to apply, but that's OK - we'll process incoming ones

        first_diff = self.diff_buffer[0]
        U = first_diff['U']
        u = first_diff['u']
        pu = first_diff.get('pu', -1)  # Futures-specific: previous u
        snapshot_id = self.last_fetched_snapshot_id

        logger.debug(f"[{self.symbol}] Validating first diff: U={U}, u={u}, pu={pu}, snapshot_id={snapshot_id}")

        # Binance rule: U <= lastUpdateId + 1 AND u >= lastUpdateId + 1
        # This means lastUpdateId + 1 must be within the range [U, u]
        if U <= snapshot_id + 1 <= u:
            logger.info(f"[{self.symbol}] First diff validated. Applying U={U}, u={u}, pu={pu} (snapshot_id={snapshot_id})")
            self.book_state.apply_diff(first_diff)
            self._last_processed_u = u  # Track for pu validation of subsequent events
            self._awaiting_first_diff = False  # First diff applied, use pu validation from now on
            self.diff_buffer.popleft()
            return True
        else:
            logger.warning(f"[{self.symbol}] First diff failed validation: U={U}, u={u}, pu={pu}, snapshot_id={snapshot_id}. Expected {snapshot_id + 1} in range [U, u].")
            return False

    def _process_sequential_diffs(self):
        """
        Process diffs that should follow strictly sequentially after initial sync.
        For USDT-M Futures: uses 'pu' (previous u) field for sequence validation.
        Exception: First diff after snapshot uses U/u range validation, not pu.
        """
        processed_count = 0
        while self.diff_buffer:
            diff_event = self.diff_buffer[0]
            current_book_id = self.book_state.last_update_id
            U = diff_event['U']
            u = diff_event['u']
            pu = diff_event.get('pu', -1)  # Futures-specific: previous u

            # Case 1: Event is outdated (already processed)
            if u <= current_book_id:
                logger.debug(f"[{self.symbol}] Dropping outdated diff (u={u}) <= book last_update_id ({current_book_id})")
                self.diff_buffer.popleft()
                continue

            # Case 2: First diff after snapshot - use U/u range validation
            if self._awaiting_first_diff:
                snapshot_id = self.last_fetched_snapshot_id
                if U <= snapshot_id + 1 <= u:
                    logger.info(f"[{self.symbol}] First diff after snapshot validated. Applying U={U}, u={u} (snapshot_id={snapshot_id})")
                    self.book_state.apply_diff(diff_event)
                    self._last_processed_u = u
                    self._awaiting_first_diff = False
                    self.diff_buffer.popleft()
                    processed_count += 1
                else:
                    # First diff doesn't match snapshot - need newer snapshot
                    logger.warning(f"[{self.symbol}] First diff failed validation: U={U}, u={u}, snapshot_id={snapshot_id}. Initiating resync.")
                    self.resync_book()
                    break
                continue

            # Case 3: Validate using 'pu' field (Futures-specific)
            # The event's 'pu' should equal our last processed event's 'u'
            if pu == self._last_processed_u:
                # Valid sequence - apply the diff
                self.book_state.apply_diff(diff_event)
                self._last_processed_u = u  # Update for next event's validation
                self.diff_buffer.popleft()
                processed_count += 1
                if processed_count % 100 == 0:  # Log every 100 diffs to reduce noise
                    logger.debug(f"[{self.symbol}] Applied {processed_count} diffs. Book lastUpdateId: {self.book_state.last_update_id}")
            else:
                # Case 4: Gap detected - pu doesn't match our last u
                logger.warning(f"[{self.symbol}] Gap detected: expected pu={self._last_processed_u}, got pu={pu} (U={U}, u={u}). Initiating resync.")
                self.resync_book()
                break

        if processed_count > 0:
            logger.info(f"[{self.symbol}] Applied {processed_count} sequential diffs. Book lastUpdateId: {self.book_state.last_update_id}")


    def handle_websocket_message(self, message: Dict):
        """
        Receives and buffers WebSocket depth update messages.
        Signals when first message arrives, and processes diffs after sync is complete.
        """
        if self._critical_failure:
            return

        # Ensure it's a depth update event
        if message.get('e') != 'depthUpdate':
            return

        # Buffer all incoming diffs
        self.diff_buffer.append(message)

        # Signal that first message has been received (for sync to proceed)
        if not self._first_message_received.is_set():
            logger.debug(f"[{self.symbol}] First WebSocket message received: U={message['U']}, u={message['u']}")
            self._first_message_received.set()

        # Log buffer size periodically
        if len(self.diff_buffer) % 50 == 0:
            logger.debug(f"[{self.symbol}] Buffer size: {len(self.diff_buffer)}")

        # Process diffs if synchronized
        if self.is_snapshot_received and not self.is_synchronizing:
            self._process_sequential_diffs()
        
    def resync_book(self):
        """
        Initiates a new synchronization process.
        Note: We do NOT clear the buffer - we keep buffering while fetching a new snapshot.
        """
        if self._critical_failure:
            logger.debug(f"[{self.symbol}] Critical failure flag set, not attempting resync.")
            return

        if self.is_synchronizing:
            logger.debug(f"[{self.symbol}] Already synchronizing, skipping redundant resync call.")
            return

        self.is_snapshot_received = False
        # Keep buffer intact - it contains valid future events
        # The new snapshot fetch will align with buffered events
        logger.warning(f"[{self.symbol}] Resyncing order book. Current buffer size: {len(self.diff_buffer)}, book last_update_id: {self.book_state.last_update_id}")
        asyncio.create_task(self._resync_with_buffer())

    async def _resync_with_buffer(self):
        """
        Resynchronize order book while keeping buffer intact.
        Fetches new snapshots until one aligns with buffered events.
        """
        async with self.snapshot_fetch_lock:
            if self.is_snapshot_received:
                logger.debug(f"[{self.symbol}] Already synchronized during lock wait.")
                return

            self.is_synchronizing = True

            for attempt in range(self.MAX_SNAPSHOT_ATTEMPTS):
                if attempt > 0:
                    delay = self.initial_reconnect_delay * (2 ** (attempt - 1))
                    logger.info(f"[{self.symbol}] Resync: fetching snapshot in {delay:.2f}s (attempt {attempt + 1}/{self.MAX_SNAPSHOT_ATTEMPTS})...")
                    await asyncio.sleep(delay)

                snapshot = await self._fetch_snapshot()
                if not snapshot:
                    logger.warning(f"[{self.symbol}] Resync: snapshot fetch failed (attempt {attempt + 1}).")
                    continue

                snapshot_last_update_id = snapshot['lastUpdateId']
                first_buffered_U = self._get_first_usable_event_U()

                if first_buffered_U is None:
                    logger.warning(f"[{self.symbol}] Resync: buffer empty, waiting for messages...")
                    await asyncio.sleep(0.2)
                    first_buffered_U = self._get_first_usable_event_U()
                    if first_buffered_U is None:
                        continue

                logger.debug(f"[{self.symbol}] Resync: snapshot lastUpdateId={snapshot_last_update_id}, first buffered U={first_buffered_U}")

                # Check if snapshot is stale
                if snapshot_last_update_id < first_buffered_U:
                    logger.info(f"[{self.symbol}] Resync: snapshot stale, fetching newer...")
                    continue

                # Initialize with snapshot
                self.book_state.initialize_with_snapshot(snapshot)
                self.last_fetched_snapshot_id = snapshot_last_update_id
                logger.info(f"[{self.symbol}] Resync: snapshot initialized with lastUpdateId: {self.last_fetched_snapshot_id}")

                # Drop outdated diffs and validate
                self._drop_outdated_diffs()

                if self._validate_and_apply_first_diff():
                    self.is_snapshot_received = True
                    self.is_synchronizing = False
                    logger.info(f"[{self.symbol}] Resync complete. Processing sequential diffs...")
                    self._process_sequential_diffs()
                    return
                else:
                    logger.warning(f"[{self.symbol}] Resync: first diff validation failed, retrying...")
                    continue

            # Exhausted attempts
            logger.critical(f"[{self.symbol}] Resync failed after {self.MAX_SNAPSHOT_ATTEMPTS} attempts.")
            self._critical_failure = True
            self.is_synchronizing = False
