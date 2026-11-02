"""Collect, permanently preserve, aggregate and export PyPI download statistics."""

from .aggregation import find_gaps, monthly, rolling_window, summarize, weekly, yearly
from .config import ReportSettings
from .errors import ConfigError, HistoryError, PartialFailure, PyPIStatsError, TrackerError
from .tracker import Tracker

__all__ = [
    "ConfigError",
    "HistoryError",
    "PartialFailure",
    "PyPIStatsError",
    "ReportSettings",
    "Tracker",
    "TrackerError",
    "find_gaps",
    "monthly",
    "rolling_window",
    "summarize",
    "weekly",
    "yearly",
]
