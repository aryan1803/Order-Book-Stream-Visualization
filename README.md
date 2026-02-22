# Real-Time Order Book Liquidity Visualization Dashboard

A research-grade, real-time L2 order book visualization system for Binance USDT-M Futures. Streams live depth data for **BTCUSDT** and **ETHUSDT**, reconstructs the order book deterministically, computes microstructure features, detects liquidity events, and renders everything in a live dashboard.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Binance](https://img.shields.io/badge/Exchange-Binance%20Futures-yellow)
![License](https://img.shields.io/badge/License-MIT-green)

## Architecture

```
Binance WebSocket (100ms depth updates)
        |
Data Ingestion Engine
        |
Order Book Reconstruction (SortedDict, sequence-validated)
        |
Feature Computation Engine (metrics, OFI, liquidity detection)
        |
Rolling State Buffer (180s in-memory)
        |
FastAPI WebSocket Server ──── REST API (health, simulate)
        |
Dash Frontend (WebGL charts, 1s update cadence)
```

## Features

### Live Order Book Reconstruction
- Deterministic L2 reconstruction following Binance's official sync protocol
- Snapshot + diff synchronization with sequence gap detection
- USDT-M Futures `pu` field validation for sequential diff integrity
- Auto-resync on sequence gaps with exponential backoff reconnection
- Top 10 bid/ask levels maintained in `SortedDict` structures

### Microstructure Metrics (computed every update)
- **Mid-price** and **Microprice** (volume-weighted)
- **Bid-Ask Spread**
- **Bid-Ask Imbalance** (range [-1, 1])
- **Order Flow Imbalance (OFI)**: `ΔBidVolume - ΔAskVolume` per second
- Depth-weighted price, order book slope, cumulative depth

### Liquidity Event Detection
- **Wall Detection** — flags levels at 5x rolling median that persist > 5 seconds
- **Layering Detection** — identifies clusters of 4+ adjacent abnormal levels with 60%+ churn rate
- **Exhaustion Detection** — warns when depth drops below 50% of average while spread doubles

### Market Impact Simulator
- Simulates market order execution against the live order book
- Walks cumulative depth to compute **VWAP**, **slippage** (absolute + bps), and per-level fill breakdown
- Accessible via REST endpoint: `GET /simulate/{symbol}?side=buy&size=1.0`

### Operational Monitoring
- Heartbeat watchdog (stale connection detection at 5s threshold)
- Per-message processing latency tracking (typical: 0.1–0.8ms)
- Update rate validation (~10/s expected from 100ms stream)
- Memory profiling via `psutil` (RSS baseline + growth alerts at 50%)
- Auto-reconnection with exponential backoff (1s → 30s cap)

### Dashboard Visualizations
- **DOM Ladder** — horizontal bar chart showing top 10 bid/ask levels, walls highlighted in orange
- **Volume Profile** — 180-second average liquidity at each price level
- **Depth Curve** — cumulative bid/ask depth with WebGL rendering
- **3D Liquidity Surface** — time × price × size surface plot (Plotly WebGL)
- **Metrics Bar** — mid price, spread, imbalance, OFI, bid/ask volume
- **Alerts Panel** — real-time liquidity event badges (walls, layering, exhaustion)
- Symbol switcher dropdown (BTCUSDT / ETHUSDT)

## Project Structure

```
├── data_ingestion/
│   ├── websocket_client.py      # Binance WebSocket connection handler
│   └── snapshot_loader.py       # REST API depth snapshot fetcher
│
├── orderbook/
│   ├── book_state.py            # SortedDict-based L2 order book
│   └── sequence_validator.py    # Diff sequencing + snapshot sync
│
├── features/
│   ├── basic_metrics.py         # Microprice, imbalance, slope, depth
│   ├── ofi.py                   # Order Flow Imbalance tracker
│   ├── liquidity_detection.py   # Wall, layering, exhaustion detection
│   └── market_impact.py         # Market order impact simulator
│
├── state/
│   ├── rolling_buffer.py        # 180s in-memory time-series buffer
│   └── level_history.py         # Per-level price/size history for charts
│
├── api/
│   └── websocket_server.py      # FastAPI server + WebSocket push
│
├── dashboard/
│   ├── app.py                   # Dash application setup
│   ├── layout.py                # UI layout definition
│   └── callbacks.py             # Chart update callbacks
│
├── config.py                    # Symbols, URLs, thresholds
├── main.py                      # Orchestrator entry point
└── requirements.txt
```

## Getting Started

### Prerequisites
- Python 3.11+
- Internet connection (streams live data from Binance)

### Installation

```bash
git clone https://github.com/aryan1803/order-book-stream-visualization.git
cd order-book-stream-visualization
pip install -r requirements.txt
```

### Running

```bash
python main.py
```

This starts three services simultaneously:

| Service | URL | Description |
|---------|-----|-------------|
| Dashboard | http://127.0.0.1:8050 | Live visualization UI |
| API Server | http://127.0.0.1:8000 | WebSocket + REST endpoints |
| WebSocket Feeds | — | Binance BTCUSDT + ETHUSDT streams |

Open **http://127.0.0.1:8050** in your browser to view the dashboard.

### Stopping

Press **Ctrl+C** in the terminal. The orchestrator handles graceful shutdown of all WebSocket connections, monitoring tasks, and servers.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| WebSocket | `/ws/{symbol}` | Real-time order book stream (1s push) |
| GET | `/health` | System health, uptime, memory, latency stats |
| GET | `/symbols` | List of available symbols |
| GET | `/stats` | Burst protection statistics |
| GET | `/simulate/{symbol}?side=buy&size=1.0` | Market impact simulation |

### Sample `/health` Response

```json
{
  "status": "healthy",
  "connections": 0,
  "symbols": ["ethusdt", "btcusdt"],
  "uptime_seconds": 32.9,
  "memory_mb": 135.9,
  "monitoring": {
    "ethusdt": { "latency_avg_ms": 0.5, "reconnect_count": 0 },
    "btcusdt": { "latency_avg_ms": 0.43, "reconnect_count": 0 }
  }
}
```

### Sample `/simulate/btcusdt?side=buy&size=5.0` Response

```json
{
  "symbol": "btcusdt",
  "side": "buy",
  "order_size": 5.0,
  "quantity_filled": 5.0,
  "vwap": 97500.12,
  "best_price": 97500.1,
  "slippage": 0.02,
  "slippage_bps": 0.0,
  "levels_consumed": 2,
  "fills": [
    { "price": 97500.1, "qty": 4.5, "cumulative_qty": 4.5, "cumulative_cost": 438750.45 },
    { "price": 97500.2, "qty": 0.5, "cumulative_qty": 5.0, "cumulative_cost": 487500.55 }
  ],
  "fully_filled": true
}
```

## WebSocket Payload

Pushed every 1 second to connected clients on `/ws/{symbol}`:

```json
{
  "symbol": "btcusdt",
  "mid_price": 97500.15,
  "spread": 0.10,
  "imbalance": 0.34,
  "ofi": 2.45,
  "microprice": 97500.18,
  "total_bid_volume": 45.23,
  "total_ask_volume": 38.91,
  "bid_levels": [[97500.10, 3.5], [97500.00, 2.1]],
  "ask_levels": [[97500.20, 1.8], [97500.30, 4.2]],
  "timestamp": 1740010520.87
}
```

## Detection Thresholds

| Detector | Parameter | Value |
|----------|-----------|-------|
| Wall | Size multiplier | 5x rolling median |
| Wall | Persistence | > 5 seconds |
| Wall | Median window | 30 seconds |
| Layering | Size multiplier | 8x rolling median |
| Layering | Min adjacent levels | 4 |
| Layering | Churn threshold | 60% new prices |
| Layering | Cooldown | 30 seconds |
| Exhaustion | Depth ratio | < 50% of 15s average |
| Exhaustion | Spread ratio | > 2x of 15s average |

## Dependencies

| Package | Purpose |
|---------|---------|
| `websockets` | Binance WebSocket connection |
| `httpx` | Async HTTP for REST snapshot |
| `sortedcontainers` | SortedDict for order book |
| `fastapi` | WebSocket + REST API server |
| `uvicorn` | ASGI server for FastAPI |
| `dash` | Frontend dashboard framework |
| `psutil` | Memory profiling |

## Notes

- **No disk persistence** — all data is held in-memory and lost on shutdown
- **No real trades** — the market impact simulator is a read-only calculator
- **No API keys required** — uses Binance public market data endpoints only
- Data source: Binance USDT-M Futures (`fstream.binance.com`)
- Depth stream: `@depth@100ms` (10 updates/second)
- Rolling buffer: 180 seconds (3 minutes)
- Dashboard update cadence: 1 second
