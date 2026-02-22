"""
Market Impact Simulator (Phase 6)

Simulates the effect of placing a market order against the current order book.
Walks cumulative depth to compute VWAP, slippage, and per-level fill breakdown.
"""


def simulate_market_order(book_state, side: str, size: float) -> dict:
    """
    Simulate a market order against the current order book state.

    Args:
        book_state: OrderBookState instance with current bids/asks.
        side: "buy" (walks asks) or "sell" (walks bids).
        size: Order size in base asset (e.g., BTC for BTCUSDT).

    Returns:
        Dict with vwap, slippage, levels_consumed, fills, etc.
    """
    if side == "buy":
        levels = list(book_state.asks.items())  # ascending price
        best_price = book_state.best_ask
    else:
        levels = list(reversed(book_state.bids.items()))  # descending price
        best_price = book_state.best_bid

    remaining = size
    total_cost = 0.0
    fills = []

    for price, qty in levels:
        if remaining <= 0:
            break

        fill_qty = min(remaining, qty)
        fill_cost = fill_qty * price
        total_cost += fill_cost
        remaining -= fill_qty

        fills.append({
            "price": price,
            "qty": round(fill_qty, 8),
            "cumulative_qty": round(size - remaining, 8),
            "cumulative_cost": round(total_cost, 4),
        })

    quantity_filled = size - remaining

    if quantity_filled <= 0:
        return {
            "symbol": book_state.symbol,
            "side": side,
            "order_size": size,
            "quantity_filled": 0.0,
            "vwap": 0.0,
            "best_price": best_price,
            "slippage": 0.0,
            "slippage_bps": 0.0,
            "levels_consumed": 0,
            "fills": [],
            "fully_filled": False,
        }

    vwap = total_cost / quantity_filled

    if side == "buy":
        slippage = vwap - best_price
    else:
        slippage = best_price - vwap

    slippage_bps = (slippage / best_price) * 10_000 if best_price > 0 else 0.0

    return {
        "symbol": book_state.symbol,
        "side": side,
        "order_size": size,
        "quantity_filled": round(quantity_filled, 8),
        "vwap": round(vwap, 4),
        "best_price": best_price,
        "slippage": round(slippage, 4),
        "slippage_bps": round(slippage_bps, 2),
        "levels_consumed": len(fills),
        "fills": fills,
        "fully_filled": remaining <= 0,
    }
