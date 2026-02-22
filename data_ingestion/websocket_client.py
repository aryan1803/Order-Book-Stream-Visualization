import asyncio
import websockets
import json
import sys
import os
import signal
from typing import Dict
import logging
import datetime # Import datetime for unique log filenames

# Create logs directory if it doesn't exist
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging to file and console
log_filename = os.path.join(log_dir, f"app_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG) # Set root logger to DEBUG to capture all messages

# Remove any existing handlers from previous basicConfig calls
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# File Handler (DEBUG level)
file_handler = logging.FileHandler(log_filename)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
file_handler.setFormatter(file_formatter)
root_logger.addHandler(file_handler)

# Console Handler (INFO level) - Optional, for real-time console feedback
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO) # Only show INFO and above on console
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__) # Get logger for this module


# Add the project root to the sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import BINANCE_WEBSOCKET_BASE_URL, SYMBOLS, WEBSOCKET_UPDATE_INTERVAL
from orderbook.book_state import OrderBookState
from orderbook.sequence_validator import SequenceValidator

# Dictionary to hold SequenceValidator instances for each symbol
# The key will be the symbol, and the value will be the SequenceValidator instance
symbol_validators: Dict[str, SequenceValidator] = {}
symbol_book_states: Dict[str, OrderBookState] = {}


async def connect_to_websocket(symbol: str, stop_event: asyncio.Event):
    """
    Connects to the Binance WebSocket stream for a given symbol and passes messages
    to the corresponding SequenceValidator.
    """
    stream_url = f"{BINANCE_WEBSOCKET_BASE_URL}/{symbol.lower()}@depth@{WEBSOCKET_UPDATE_INTERVAL}"
    logger.info(f"[{symbol}] Connecting to {stream_url}")
    try:
        async with websockets.connect(stream_url) as ws:
            while not stop_event.is_set():
                try:
                    message_raw = await asyncio.wait_for(ws.recv(), timeout=1)
                    message = json.loads(message_raw)
                    # Pass the message to the appropriate SequenceValidator
                    if symbol in symbol_validators:
                        symbol_validators[symbol].handle_websocket_message(message)
                except asyncio.TimeoutError:
                    # No message received within timeout, check stop_event again
                    pass
                except json.JSONDecodeError:
                    logger.warning(f"[{symbol}] Failed to decode JSON message: {message_raw[:100]}...")
                except Exception as e:
                    logger.error(f"[{symbol}] Error processing message: {e}", exc_info=True)
    except websockets.exceptions.ConnectionClosedOK:
        logger.info(f"[{symbol}] WebSocket connection closed gracefully.")
    except Exception as e:
        logger.error(f"[{symbol}] WebSocket connection error: {e}", exc_info=True)
    finally:
        logger.info(f"[{symbol}] Disconnected from WebSocket.")


async def main():
    """
    Main function to start WebSocket connections for all configured symbols,
    initialize their SequenceValidators, and handle graceful shutdown.
    """
    stop_event = asyncio.Event()

    # Handle Ctrl+C and other signals for graceful shutdown
    def signal_handler():
        logger.info("\nCtrl+C received. Initiating graceful shutdown...")
        stop_event.set()

    # Register signal handler for SIGINT (Ctrl+C)
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)
    else:
        # On Windows, KeyboardInterrupt is usually sufficient for simple scripts.
        pass

    # Initialize OrderBookState and SequenceValidator for each symbol
    for symbol in SYMBOLS:
        book_state = OrderBookState(symbol)
        validator = SequenceValidator(symbol, book_state)
        symbol_book_states[symbol] = book_state
        symbol_validators[symbol] = validator

    # Step 1: Start WebSocket connections FIRST (per Binance docs)
    # WebSocket must be receiving and buffering messages before we fetch snapshots
    websocket_tasks = []
    for symbol in SYMBOLS:
        websocket_tasks.append(asyncio.create_task(connect_to_websocket(symbol, stop_event)))

    # Brief delay to allow WebSocket connections to establish
    await asyncio.sleep(0.5)

    # Step 2: Start synchronization for each symbol
    # The synchronization will wait for the first WebSocket message before fetching snapshot
    for symbol in SYMBOLS:
        asyncio.create_task(symbol_validators[symbol].start_synchronization())

    try:
        # Wait for all WebSocket tasks to complete or for the stop_event to be set
        await asyncio.gather(*websocket_tasks, return_exceptions=True)
    except KeyboardInterrupt:
        logger.info("\nKeyboardInterrupt detected. Setting stop event...")
        stop_event.set()
    finally:
        # Ensure all tasks are cancelled and awaited if they are still running
        for task in websocket_tasks:
            task.cancel()
        await asyncio.gather(*websocket_tasks, return_exceptions=True)
        logger.info("All WebSocket connections and related tasks gracefully closed.")
        # Print final state for debugging/validation
        for symbol, book in symbol_book_states.items():
            logger.info(f"\nFinal Order Book State for {symbol}:")
            logger.info(f"  Best Bid: {book.best_bid}, Best Ask: {book.best_ask}, Mid Price: {book.mid_price}, Spread: {book.spread}")
            # logger.debug(f"  Bids: {book.bids.items()[:5]}") # Print top 5 bids
            # logger.debug(f"  Asks: {book.asks.items()[:5]}") # Print top 5 asks

if __name__ == "__main__":
    asyncio.run(main())
