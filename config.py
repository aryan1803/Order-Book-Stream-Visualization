# config.py

BINANCE_WEBSOCKET_BASE_URL = "wss://fstream.binance.com/ws"
BINANCE_REST_API_BASE_URL = "https://fapi.binance.com/fapi/v1" # Base URL for REST API
SYMBOLS = ["btcusdt", "ethusdt"]
WEBSOCKET_UPDATE_INTERVAL = "100ms" # As specified in instructions

# For order book reconstruction
ORDER_BOOK_DEPTH_LIMIT = 10 # Top 10 levels
SNAPSHOT_LIMIT = 1000 # Limit for the depth snapshot, as per Binance API docs for /fapi/v1/depth
