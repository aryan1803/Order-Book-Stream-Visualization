"""
Dashboard Layout Module (Phase 4)

Defines the Dash HTML layout with:
- Symbol selector dropdown (BTCUSDT / ETHUSDT)
- Metrics bar (mid price, spread, imbalance, OFI)
- DOM Ladder + Volume Profile (side by side)
- Depth Curve + 3D Surface (side by side)
- dcc.Interval at 1 second for updates
"""

from dash import html, dcc
import plotly.graph_objects as go


def _create_empty_dom_ladder():
    """Initial empty DOM ladder figure."""
    fig = go.Figure()
    fig.update_layout(
        xaxis_title="Size",
        yaxis_title="Price",
        template="plotly_dark",
        uirevision="dom",
        margin=dict(l=80, r=20, t=10, b=40),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
    )
    return fig


def _create_empty_volume_profile():
    """Initial empty volume profile figure."""
    fig = go.Figure()
    fig.update_layout(
        xaxis_title="Avg Size",
        yaxis_title="Price",
        template="plotly_dark",
        uirevision="volprofile",
        margin=dict(l=80, r=20, t=10, b=40),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#1a1a2e",
    )
    return fig


def _create_empty_depth():
    """Initial empty depth curve figure."""
    fig = go.Figure()
    fig.add_trace(go.Scattergl(
        x=[], y=[], name="Bids", mode="lines",
        fill="tozeroy", fillcolor="rgba(0,200,80,0.2)",
        line=dict(color="#00c853", width=2),
    ))
    fig.add_trace(go.Scattergl(
        x=[], y=[], name="Asks", mode="lines",
        fill="tozeroy", fillcolor="rgba(255,60,60,0.2)",
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
        legend=dict(x=0.01, y=0.99),
    )
    return fig


def _create_empty_surface():
    """Initial empty 3D surface figure."""
    fig = go.Figure(data=go.Surface(
        z=[[]],
        colorscale="Viridis",
        colorbar=dict(title="Size", titleside="right"),
    ))
    fig.update_layout(
        scene=dict(
            xaxis_title="Time",
            yaxis_title="Price",
            zaxis_title="Size",
            bgcolor="#1a1a2e",
        ),
        template="plotly_dark",
        uirevision="surface",
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="#1a1a2e",
    )
    return fig


# Styles
CARD_STYLE = {
    "backgroundColor": "#16213e",
    "borderRadius": "8px",
    "padding": "12px 20px",
    "textAlign": "center",
    "minWidth": "140px",
}

METRIC_LABEL_STYLE = {
    "color": "#8892b0",
    "fontSize": "11px",
    "fontWeight": "600",
    "textTransform": "uppercase",
    "letterSpacing": "1px",
    "marginBottom": "4px",
}

METRIC_VALUE_STYLE = {
    "color": "#e6f1ff",
    "fontSize": "18px",
    "fontWeight": "700",
    "fontFamily": "monospace",
}


def create_layout():
    """Build the complete dashboard layout."""
    return html.Div([

        # Header row: title + symbol selector
        html.Div([
            html.H2(
                "Order Book Liquidity Dashboard",
                style={"color": "#e6f1ff", "margin": "0", "fontWeight": "600"},
            ),
            html.Div([
                dcc.Dropdown(
                    id="symbol-dropdown",
                    options=[
                        {"label": "BTCUSDT", "value": "btcusdt"},
                        {"label": "ETHUSDT", "value": "ethusdt"},
                    ],
                    value="btcusdt",
                    clearable=False,
                    style={
                        "width": "160px",
                        "backgroundColor": "#16213e",
                        "color": "#e6f1ff",
                    },
                ),
            ]),
        ], style={
            "display": "flex",
            "justifyContent": "space-between",
            "alignItems": "center",
            "padding": "12px 20px",
            "marginBottom": "10px",
        }),

        # Metrics bar
        html.Div(id="metrics-bar", children=[
            _metric_card("mid-price", "Mid Price", "--"),
            _metric_card("spread", "Spread", "--"),
            _metric_card("imbalance", "Imbalance", "--"),
            _metric_card("ofi", "OFI", "--"),
            _metric_card("bid-vol", "Bid Volume", "--"),
            _metric_card("ask-vol", "Ask Volume", "--"),
        ], style={
            "display": "flex",
            "gap": "10px",
            "padding": "0 20px",
            "marginBottom": "10px",
            "flexWrap": "wrap",
            "justifyContent": "center",
        }),

        # Alerts panel (Phase 5 detection events)
        html.Div(id="alerts-panel", children=[
            html.Div("No active alerts", style={
                "color": "#4a5568", "fontSize": "12px", "fontStyle": "italic",
            }),
        ], style={
            "display": "flex",
            "gap": "8px",
            "padding": "0 20px",
            "marginBottom": "10px",
            "flexWrap": "wrap",
            "minHeight": "32px",
            "alignItems": "center",
        }),

        # Row 1: DOM Ladder + Volume Profile (side by side)
        html.Div([
            # DOM Ladder
            html.Div([
                html.Div("DOM LADDER", style={
                    "color": "#8892b0", "fontSize": "11px", "fontWeight": "600",
                    "letterSpacing": "1px", "padding": "8px 12px",
                }),
                dcc.Graph(
                    id="dom-ladder-chart",
                    figure=_create_empty_dom_ladder(),
                    config={"displayModeBar": False},
                    style={"height": "420px"},
                ),
            ], style={
                "backgroundColor": "#1a1a2e",
                "borderRadius": "8px",
                "flex": "1",
            }),

            # Volume Profile
            html.Div([
                html.Div("VOLUME PROFILE (180s AVG)", style={
                    "color": "#8892b0", "fontSize": "11px", "fontWeight": "600",
                    "letterSpacing": "1px", "padding": "8px 12px",
                }),
                dcc.Graph(
                    id="volume-profile-chart",
                    figure=_create_empty_volume_profile(),
                    config={"displayModeBar": False},
                    style={"height": "420px"},
                ),
            ], style={
                "backgroundColor": "#1a1a2e",
                "borderRadius": "8px",
                "flex": "1",
            }),
        ], style={
            "display": "flex",
            "gap": "10px",
            "padding": "0 20px",
            "marginBottom": "10px",
        }),

        # Row 2: Depth Curve + 3D Surface
        html.Div([
            # Depth Curve
            html.Div([
                html.Div("DEPTH CURVE", style={
                    "color": "#8892b0", "fontSize": "11px", "fontWeight": "600",
                    "letterSpacing": "1px", "padding": "8px 12px",
                }),
                dcc.Graph(
                    id="depth-chart",
                    figure=_create_empty_depth(),
                    config={"displayModeBar": False},
                    style={"height": "380px"},
                ),
            ], style={
                "backgroundColor": "#1a1a2e",
                "borderRadius": "8px",
                "flex": "1",
            }),

            # 3D Surface
            html.Div([
                html.Div("3D LIQUIDITY SURFACE", style={
                    "color": "#8892b0", "fontSize": "11px", "fontWeight": "600",
                    "letterSpacing": "1px", "padding": "8px 12px",
                }),
                dcc.Graph(
                    id="surface-chart",
                    figure=_create_empty_surface(),
                    config={"displayModeBar": False},
                    style={"height": "380px"},
                ),
            ], style={
                "backgroundColor": "#1a1a2e",
                "borderRadius": "8px",
                "flex": "1",
            }),
        ], style={
            "display": "flex",
            "gap": "10px",
            "padding": "0 20px",
        }),

        # Interval for 1-second updates
        dcc.Interval(
            id="update-interval",
            interval=1000,
            n_intervals=0,
        ),

    ], style={
        "backgroundColor": "#0a0a1a",
        "minHeight": "100vh",
        "fontFamily": "'Inter', 'Segoe UI', sans-serif",
        "padding": "10px 0",
    })


def _metric_card(metric_id: str, label: str, default_value: str):
    """Create a single metric display card."""
    return html.Div([
        html.Div(label, style=METRIC_LABEL_STYLE),
        html.Div(default_value, id=f"metric-{metric_id}", style=METRIC_VALUE_STYLE),
    ], style=CARD_STYLE)
