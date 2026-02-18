# I want to build a Order Book Liquidity Visualization Dashboard for BTC which gets a continuous stream of data with the help of the Binance API and Websocket.

## Project: Real-Time Order Book Liquidity Visualization Dashboard

Exchange: Binance USDT-M Futures
Symbols: BTCUSDT, ETHUSDT
Mode: Quantitative Research / Academic Microstructure Modeling
Depth: Top 10 Levels
UI Update Frequency: 1 second
Environment: Local Desktop

---

# 🔒 GLOBAL RULES (MANDATORY)

1. Do NOT implement future phases prematurely.
2. Do NOT add features not explicitly listed in the current phase.
3. Do NOT optimize prematurely.
4. Do NOT store data to disk unless explicitly instructed.
5. Do NOT change architecture decisions.
6. Do NOT invent Binance fields — strictly follow official API schema.
7. Always produce modular, testable code.
8. Use asyncio for all WebSocket operations.
9. Maintain deterministic order book reconstruction logic.
10. Each phase must end with a validation checklist.

---

# 🏗 OVERALL ARCHITECTURE

Layered design:

```
Binance WebSocket
        ↓
Data Ingestion Engine
        ↓
Order Book Reconstruction Engine
        ↓
Feature Computation Engine
        ↓
Rolling State Buffer (Memory Only)
        ↓
FastAPI WebSocket Server
        ↓
Dash Frontend (WebGL Charts)
```

No cross-layer coupling.

---

# 📦 PROJECT STRUCTURE

```
project/
│
├── data_ingestion/
│   ├── websocket_client.py
│   ├── snapshot_loader.py
│
├── orderbook/
│   ├── book_state.py
│   ├── sequence_validator.py
│
├── features/
│   ├── basic_metrics.py
│   ├── ofi.py
│   ├── liquidity_detection.py
│
├── state/
│   ├── rolling_buffer.py
│
├── api/
│   ├── websocket_server.py
│
├── dashboard/
│   ├── app.py
│   ├── layout.py
│   ├── callbacks.py
│
├── config.py
├── main.py
└── requirements.txt
```

---

# 🚀 PHASE PLAN

---

# 🟢 PHASE 1 — Core Infrastructure & Order Book Integrity

### Objective

Build deterministic L2 order book reconstruction for:

* BTCUSDT
* ETHUSDT

From:
Binance
USDT-M Futures WebSocket stream.

---

## 1.1 WebSocket Connection

Use:

```
wss://fstream.binance.com/ws/<symbol>@depth@100ms
```

Symbols:

* btcusdt
* ethusdt

Use asyncio.

---

## 1.2 Snapshot + Diff Synchronization

Follow Binance official steps strictly:

1. Connect WebSocket
2. Buffer diff events
3. Fetch REST snapshot
4. Drop outdated events
5. Apply diffs sequentially
6. Ensure:

   * first event U <= lastUpdateId <= u
7. If sequence gap detected:

   * Reset book
   * Reinitialize

Implement:

* Gap detection
* Auto resync
* Exponential reconnect

---

## 1.3 Order Book Data Structure

Use:

```
SortedDict for bids (descending)
SortedDict for asks (ascending)
```

For each diff:

* size == 0 → remove
* size > 0 → update

After update:

* Keep only top 10 levels
* Maintain:

```
best_bid
best_ask
mid_price
spread
total_bid_volume
total_ask_volume
```

---

## 1.4 Validation Requirements

Before moving to Phase 2:

* Spread always positive
* Bids < Asks invariant
* No sequence gaps
* Auto resync works
* Print mid price continuously
* Confirm updates at ~100ms

---

# 🟢 PHASE 2 — Feature Engine (Basic Microstructure Metrics)

Do NOT add advanced detection yet.

---

## 2.1 Compute (Every Update)

* Spread
* Mid-price
* Microprice
* Bid-Ask Imbalance
* Depth-weighted price
* Order book slope
* Cumulative depth (10 levels)

---

## 2.2 Order Flow Tracking

Track volume changes per update:

* ΔBidVolume
* ΔAskVolume

Implement Order Flow Imbalance (OFI):

```
OFI = ΔBidVolume − ΔAskVolume
```

---

## 2.3 Rolling State Buffer

Create in-memory rolling buffer:

* 180 seconds
* 1-second aggregation
* Store:

```
timestamp
mid_price
spread
imbalance
OFI
total_bid_volume
total_ask_volume
```

No disk persistence.

---

## 2.4 Validation

* Values update every second
* No NaN values
* Buffer caps correctly
* OFI sign behavior verified manually

---

# 🟢 PHASE 3 — FastAPI Backend + WebSocket Push

---

## 3.1 Build FastAPI Server

Expose:

```
/ws/{symbol}
```

Push JSON snapshot every 1 second:

```
{
  "mid_price": float,
  "spread": float,
  "imbalance": float,
  "ofi": float,
  "bid_levels": [[price, size], ...],
  "ask_levels": [[price, size], ...]
}
```

---

## 3.2 Multi-Symbol Support

* Separate engine per symbol
* Unified API server

---

## 3.3 Burst Protection

Implement:

* asyncio.Queue(maxsize=N)
* If overflow:

  * Drop intermediate diffs
  * Keep most recent

Log burst events.

---

## 3.4 Validation

* WebSocket pushes stable 1-second updates
* No memory leak
* Both BTC + ETH functional
* Auto resync verified

---

# 🟢 PHASE 4 — Dash Visualization Layer

Use WebGL for all plots.

---

## 4.1 Animated Liquidity Heatmap

![Image](https://d2nzipe0469gd2.cloudfront.net/uploads/c1ee8353-313d-4d8d-bab3-cd35f5d67244.png)

![Image](https://www.timestored.com/pulse/help/img/chart-depthmap.png)

![Image](https://bookmap.com/_next/image?q=75\&url=%2Fstatic%2Ffeatures%2Fv2%2Fs2%2Fs2.webp\&w=3840)

![Image](https://optimusfutures.com/img/Bookmap/Bookmap-2.jpg)

X: Time
Y: Price
Color: Size

Use rolling 180-second window.

---

## 4.2 Depth Curve

![Image](https://whaleportal.com/media/uploads/2024/09/23/screenshot-2024-09-22-175511-2.gif)

![Image](https://miro.medium.com/1%2ANjNZwgGH-jisDvbk7GvrOw.png)

![Image](https://miro.medium.com/v2/resize%3Afit%3A1200/1%2ANjNZwgGH-jisDvbk7GvrOw.png)

![Image](https://ninjatrader.com/getattachment/a8ed50c0-faa2-4245-a5f5-cc638b6ffddb/DepthCharts.png)

Plot:

* Cumulative bids
* Cumulative asks

---

## 4.3 3D Surface Plot

![Image](https://altair.com/images/default-source/resource-images/demo-visualize-order-books-w-full-depth-1200x628-png.png?sfvrsn=2c61ebe_0)

![Image](https://www.msci.com/_next/image?q=80\&url=%2Fassets%2Fweb%2Fmsci-com%2Fresearch-and-insights%2Fblog-post%2Fmeasuring-liquidity-risk%2FLiquidity-metrics.PNG.png\&w=3840)

![Image](https://www.scichart.com/demo/images/javascript-depth-chart.jpg)

![Image](https://www.researchgate.net/publication/2871052/figure/fig8/AS%3A668583684235267%401536414180425/A-3D-area-chart-showing-the-fluctuations-in-share-price-in-a-particular-portfolio-over-12.png)

Use Plotly Surface with WebGL.

---

## 4.4 UI Constraints

* Update every second only
* No polling
* WebSocket push only
* No blocking callbacks

---

## 4.5 Validation

* Smooth rendering
* CPU stable
* No browser freeze
* No full DOM redraw

---

# 🟢 PHASE 5 — Advanced Microstructure Detection

Now add:

---

## 5.1 Liquidity Wall Detection

If:

```
level_size > 3 × rolling median level size
AND persists > 5 seconds
```

Flag as wall.

---

## 5.2 Sweep Detection

If:

* Trade consumes ≥ 3 levels
* Occurs within < 200ms

Mark sweep event.

---

## 5.3 Absorption Detection

If:

* Aggressive trades occur
* Price does not move
* Opposite side replenishes

Flag absorption.

---

## 5.4 Layering Detection

If:

* Multiple adjacent levels show abnormal size
* Frequently modified

Flag layering suspicion.

---

## 5.5 Iceberg Heuristic

If:

* Repeated trades at same price
* Visible depth does not decrease proportionally

Mark iceberg suspicion.

---

## 5.6 Liquidity Exhaustion

If:

* Rapid thinning before breakout
* Spread widens

Mark exhaustion.

---

## 5.7 Validation

* Flags appear logically
* No constant false positives
* Edge cases tested

---

# 🟢 PHASE 6 — Market Impact Simulator

Add function:

```
simulate_market_order(symbol, size)
```

Algorithm:

* Walk cumulative depth
* Compute VWAP
* Compute slippage
* Return:

```
{
  avg_price,
  slippage,
  levels_consumed
}
```

Expose via FastAPI endpoint.

---

# 🟢 PHASE 7 — Code Quality & Stability Hardening

Add:

* Heartbeat watchdog
* Latency logging
* Update rate monitoring
* Memory profiling
* Reconnection stress testing

---

# 🔮 FUTURE (DO NOT IMPLEMENT YET)

* Historical replay
* Disk persistence
* ML feature export
* Multi-exchange comparison
* Hawkes process modeling
* Execution optimization

---

# ✅ COMPLETION CHECKLIST

Before declaring complete:

* Deterministic L2 reconstruction verified
* No sequence gaps
* Resync logic functional
* Features stable
* Heatmap smooth
* CPU usage acceptable
* BTC + ETH working
* No memory growth over 30 min test

---

# 🎯 FINAL SYSTEM CHARACTERISTICS

* Research-grade integrity
* Deterministic microstructure state
* Real-time 10-level L2
* 1-second visualization cadence
* Modular expandable design
* Medium-performance architecture

---