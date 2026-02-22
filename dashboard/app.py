"""
Dash Application Module (Phase 4)

Creates and configures the Dash application instance.
Data sources are injected by the orchestrator via set_data_sources().
"""

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import dash
from dashboard.layout import create_layout

# Create the Dash app
app = dash.Dash(
    __name__,
    update_title=None,  # Prevent "Updating..." flash in browser tab
    suppress_callback_exceptions=True,
)
app.title = "Order Book Liquidity Dashboard"

# Shared data source references (populated by orchestrator before server starts)
_data_sources = {
    "book_states": {},
    "rolling_buffers": {},
    "level_histories": {},
    "metrics_calculators": {},
    "symbols": [],
    "detection_events": {},
}


def set_data_sources(book_states, rolling_buffers, level_histories,
                     metrics_calculators, symbols, detection_events=None):
    """
    Called by orchestrator to inject shared-memory data references.
    """
    _data_sources["book_states"] = book_states
    _data_sources["rolling_buffers"] = rolling_buffers
    _data_sources["level_histories"] = level_histories
    _data_sources["metrics_calculators"] = metrics_calculators
    _data_sources["symbols"] = symbols
    if detection_events is not None:
        _data_sources["detection_events"] = detection_events


def get_data_sources():
    """Access the shared data sources from callbacks."""
    return _data_sources


# Set layout
app.layout = create_layout()

# Import callbacks (must be after app is created so decorators can register)
from dashboard import callbacks  # noqa: E402, F401
