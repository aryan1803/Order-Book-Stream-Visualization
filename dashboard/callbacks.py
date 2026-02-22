"""
Dashboard Callbacks Module (Phase 4 + Phase 5)

Defines Dash callbacks that update all charts, metrics, and alerts every 1 second.
Triggered by dcc.Interval, reads from shared-memory data sources.
"""

import datetime
from dash import html, Output, Input
from dash.exceptions import PreventUpdate
import plotly.graph_objects as go

from dashboard.app import app, get_data_sources

# Alert badge styles
_ALERT_STYLES = {
    "WALL": {
        "backgroundColor": "#e65100",
        "color": "white",
        "padding": "4px 12px",
        "borderRadius": "12px",
        "fontSize": "11px",
        "fontWeight": "600",
        "fontFamily": "monospace",
    },
    "LAYERING": {
        "backgroundColor": "#f9a825",
        "color": "#1a1a2e",
        "padding": "4px 12px",
        "borderRadius": "12px",
        "fontSize": "11px",
        "fontWeight": "600",
        "fontFamily": "monospace",
    },
    "EXHAUSTION": {
        "backgroundColor": "#c62828",
        "color": "white",
        "padding": "4px 12px",
        "borderRadius": "12px",
        "fontSize": "11px",
        "fontWeight": "600",
        "fontFamily": "monospace",
    },
}


@app.callback(
    # Charts
    Output("dom-ladder-chart", "figure"),
    Output("volume-profile-chart", "figure"),
    Output("depth-chart", "figure"),
    Output("surface-chart", "figure"),
    # Metrics bar
    Output("metric-mid-price", "children"),
    Output("metric-spread", "children"),
    Output("metric-imbalance", "children"),
    Output("metric-ofi", "children"),
    Output("metric-bid-vol", "children"),
    Output("metric-ask-vol", "children"),
    # Alerts
    Output("alerts-panel", "children"),
    # Triggers
    Input("update-interval", "n_intervals"),
    Input("symbol-dropdown", "value"),
    prevent_initial_call=False,
)
def update_dashboard(n_intervals, symbol):
    """Update all charts, metrics, and alerts for the selected symbol."""
    sources = get_data_sources()
    level_history = sources["level_histories"].get(symbol)
    book_state = sources["book_states"].get(symbol)
    rolling_buffer = sources["rolling_buffers"].get(symbol)
    metrics_calc = sources["metrics_calculators"].get(symbol)

    if not level_history or not book_state or level_history.size == 0:
        raise PreventUpdate

    # Compute current metrics
    metrics = metrics_calc.compute_all_metrics(book_state)

    # Get OFI from rolling buffer
    latest_ofi = 0.0
    if rolling_buffer:
        latest = rolling_buffer.get_latest()
        if latest:
            latest_ofi = latest.ofi

    # Get detection events
    detection_events = sources.get("detection_events", {}).get(symbol, [])

    # Collect wall prices for DOM ladder highlighting
    wall_prices = set()
    for evt in detection_events:
        if evt.event_type == "WALL" and evt.price is not None:
            wall_prices.add((evt.side, evt.price))

    # Build all charts
    dom_fig = _build_dom_ladder(level_history, symbol, wall_prices)
    vol_fig = _build_volume_profile(level_history, symbol)
    depth_fig = _build_depth_curve(level_history, symbol)
    surface_fig = _build_surface(level_history, symbol)

    # Build alerts
    alerts_children = _build_alerts(detection_events)

    # Format metrics
    mid_str = f"{metrics.mid_price:,.2f}"
    spread_str = f"{metrics.spread:.4f}"
    imbal_str = f"{metrics.bid_ask_imbalance:+.3f}"
    ofi_str = f"{latest_ofi:+.2f}"
    bid_vol_str = f"{metrics.total_bid_volume:.2f}"
    ask_vol_str = f"{metrics.total_ask_volume:.2f}"

    return (
        dom_fig, vol_fig, depth_fig, surface_fig,
        mid_str, spread_str, imbal_str, ofi_str, bid_vol_str, ask_vol_str,
        alerts_children,
    )


def _build_alerts(events) -> list:
    """Build alert badge components from detection events."""
    if not events:
        return [html.Div("No active alerts", style={
            "color": "#4a5568", "fontSize": "12px", "fontStyle": "italic",
        })]

    badges = []
    for evt in events:
        style = _ALERT_STYLES.get(evt.event_type, _ALERT_STYLES["WALL"])
        label = evt.details
        badges.append(html.Span(label, style=style))

    return badges


def _build_dom_ladder(level_history, symbol: str, wall_prices=None) -> go.Figure:
    """
    Build the DOM (Depth of Market) ladder.

    Horizontal bar chart: bids extend left (green), asks extend right (red).
    Wall prices are highlighted in orange.
    """
    if wall_prices is None:
        wall_prices = set()

    bid_levels, ask_levels, mid_price = level_history.get_dom_ladder()

    if not bid_levels and not ask_levels:
        return go.Figure()

    fig = go.Figure()

    # Bids: extend to the LEFT (negative x), green
    bid_prices = [p for p, _ in reversed(bid_levels)]
    bid_sizes = [s for _, s in reversed(bid_levels)]
    best_bid = bid_levels[0][0] if bid_levels else 0

    bid_colors = []
    for p in bid_prices:
        if ("BID", p) in wall_prices:
            bid_colors.append("#e65100")  # Orange for walls
        elif p == best_bid:
            bid_colors.append("#00e676")  # Bright green for best
        else:
            bid_colors.append("#00c853")

    fig.add_trace(go.Bar(
        y=[f"{p:,.2f}" for p in bid_prices],
        x=[-s for s in bid_sizes],
        orientation="h",
        name="Bids",
        marker=dict(color=bid_colors),
        text=[f"{s:.3f}" for s in bid_sizes],
        textposition="inside",
        textfont=dict(color="white", size=11),
        hovertemplate="Price: %{y}<br>Size: %{text}<extra>Bid</extra>",
    ))

    # Asks: extend to the RIGHT (positive x), red
    ask_prices = [p for p, _ in ask_levels]
    ask_sizes = [s for _, s in ask_levels]
    best_ask = ask_levels[0][0] if ask_levels else 0

    ask_colors = []
    for p in ask_prices:
        if ("ASK", p) in wall_prices:
            ask_colors.append("#e65100")  # Orange for walls
        elif p == best_ask:
            ask_colors.append("#ff5252")  # Bright red for best
        else:
            ask_colors.append("#ff1744")

    fig.add_trace(go.Bar(
        y=[f"{p:,.2f}" for p in ask_prices],
        x=ask_sizes,
        orientation="h",
        name="Asks",
        marker=dict(color=ask_colors),
        text=[f"{s:.3f}" for s in ask_sizes],
        textposition="inside",
        textfont=dict(color="white", size=11),
        hovertemplate="Price: %{y}<br>Size: %{text}<extra>Ask</extra>",
    ))

    # Symmetric x-axis
    all_sizes = bid_sizes + ask_sizes
    max_size = max(all_sizes) if all_sizes else 1.0
    x_range = max_size * 1.15

    fig.update_layout(
        barmode="overlay",
        xaxis=dict(
            range=[-x_range, x_range],
            title="Size",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.3)",
            zerolinewidth=1,
            tickvals=[-x_range * 0.75, -x_range * 0.5, -x_range * 0.25,
                       0,
                       x_range * 0.25, x_range * 0.5, x_range * 0.75],
            ticktext=[f"{x_range * 0.75:.2f}", f"{x_range * 0.5:.2f}", f"{x_range * 0.25:.2f}",
                      "0",
                      f"{x_range * 0.25:.2f}", f"{x_range * 0.5:.2f}", f"{x_range * 0.75:.2f}"],
        ),
        yaxis=dict(
            title="Price",
            type="category",
            showgrid=False,
        ),
        template="plotly_dark",
        uirevision="dom",
        margin=dict(l=90, r=20, t=10, b=40),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)"),
        bargap=0.08,
    )

    return fig


def _build_volume_profile(level_history, symbol: str) -> go.Figure:
    """
    Build the volume profile — average liquidity at each price over 180s.
    """
    bid_profile, ask_profile = level_history.get_volume_profile()

    if not bid_profile and not ask_profile:
        return go.Figure()

    fig = go.Figure()

    if bid_profile:
        bp_prices = [f"{p:,.2f}" for p, _ in bid_profile]
        bp_sizes = [s for _, s in bid_profile]

        fig.add_trace(go.Bar(
            y=bp_prices,
            x=[-s for s in bp_sizes],
            orientation="h",
            name="Bid Avg",
            marker=dict(color="#00c853", opacity=0.7),
            hovertemplate="Price: %{y}<br>Avg Size: %{text}<extra>Bid</extra>",
            text=[f"{s:.3f}" for s in bp_sizes],
            textposition="none",
        ))

    if ask_profile:
        ap_prices = [f"{p:,.2f}" for p, _ in ask_profile]
        ap_sizes = [s for _, s in ask_profile]

        fig.add_trace(go.Bar(
            y=ap_prices,
            x=ap_sizes,
            orientation="h",
            name="Ask Avg",
            marker=dict(color="#ff1744", opacity=0.7),
            hovertemplate="Price: %{y}<br>Avg Size: %{text}<extra>Ask</extra>",
            text=[f"{s:.3f}" for s in ap_sizes],
            textposition="none",
        ))

    all_sizes = ([s for _, s in bid_profile] if bid_profile else []) + \
                ([s for _, s in ask_profile] if ask_profile else [])
    max_size = max(all_sizes) if all_sizes else 1.0
    x_range = max_size * 1.15

    fig.update_layout(
        barmode="overlay",
        xaxis=dict(
            range=[-x_range, x_range],
            title="Avg Size (180s)",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=True,
            zerolinecolor="rgba(255,255,255,0.3)",
            zerolinewidth=1,
        ),
        yaxis=dict(
            title="Price",
            type="category",
            showgrid=False,
        ),
        template="plotly_dark",
        uirevision="volprofile",
        margin=dict(l=90, r=20, t=10, b=40),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)"),
        bargap=0.05,
    )

    return fig


def _build_depth_curve(level_history, symbol: str) -> go.Figure:
    """Build the cumulative depth curve figure."""
    bid_prices, bid_cum, ask_prices, ask_cum = level_history.get_depth_curve()

    fig = go.Figure()

    if bid_prices:
        fig.add_trace(go.Scattergl(
            x=bid_prices,
            y=bid_cum,
            name="Bids",
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(0,200,80,0.25)",
            line=dict(color="#00c853", width=2),
        ))

    if ask_prices:
        fig.add_trace(go.Scattergl(
            x=ask_prices,
            y=ask_cum,
            name="Asks",
            mode="lines",
            fill="tozeroy",
            fillcolor="rgba(255,60,60,0.25)",
            line=dict(color="#ff1744", width=2),
        ))

    fig.update_layout(
        xaxis_title="Price",
        yaxis_title="Cumulative Size",
        template="plotly_dark",
        uirevision="depth",
        margin=dict(l=60, r=20, t=10, b=40),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
    )

    return fig


def _build_surface(level_history, symbol: str) -> go.Figure:
    """Build the 3D liquidity surface figure."""
    timestamps, prices, z_abs = level_history.get_surface_data()

    if not timestamps or not prices:
        return go.Figure()

    time_labels = [
        datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        for ts in timestamps
    ]

    fig = go.Figure(data=go.Surface(
        z=z_abs,
        x=list(range(len(time_labels))),
        y=prices,
        colorscale="Viridis",
        colorbar=dict(
            title=dict(text="Size", side="right"),
            thickness=15,
            len=0.9,
        ),
        hovertemplate="Time idx: %{x}<br>Price: %{y:,.2f}<br>Size: %{z:.4f}<extra></extra>",
    ))

    fig.update_layout(
        scene=dict(
            xaxis_title="Time",
            yaxis_title="Price",
            zaxis_title="Size",
            bgcolor="#1a1a2e",
            xaxis=dict(
                tickvals=list(range(0, len(time_labels), max(1, len(time_labels) // 6))),
                ticktext=[time_labels[i] for i in range(0, len(time_labels), max(1, len(time_labels) // 6))],
            ),
        ),
        template="plotly_dark",
        uirevision="surface",
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="#1a1a2e",
    )

    return fig
