"""Pure calculations over daily observations. Missing days are never treated as zero."""

import calendar
from collections.abc import Callable, Iterable, Iterator
from datetime import date, timedelta

from .storage import History

_ONE_DAY = timedelta(days=1)


def combine(histories: Iterable[History]) -> History:
    """Add packages together. A date counts when any package has it; absent packages add nothing."""
    combined: History = {}
    for history in histories:
        for day, downloads in history.items():
            combined[day] = combined.get(day, 0) + downloads
    return combined


def tally(history: History, start: date, end: date) -> dict:
    """Total an inclusive date range. `downloads` is None when no day in it was recorded."""
    days = (end - start).days + 1
    values = [history[day] for day in _dates(start, end) if day in history]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "downloads": sum(values) if values else None,
        "recorded_days": len(values),
        "missing_days": days - len(values),
        "complete": len(values) == days,
    }


def rolling_window(history: History, days: int) -> dict:
    """Tally the `days` days ending at the latest recorded date, not at today's date.

    `observations` has one entry per day in the window, so charts always get `days` points.
    A day with no data has `downloads: None`. With no history at all the list is empty.
    """
    if days < 1:
        raise ValueError(f"days must be positive, got {days}")
    if not history:
        return {
            "start": None,
            "end": None,
            "days": days,
            "downloads": None,
            "recorded_days": 0,
            "missing_days": days,
            "complete": False,
            "observations": [],
        }
    end = max(history)
    start = end - _ONE_DAY * (days - 1)
    return {
        **tally(history, start, end),
        "observations": [
            {"date": day.isoformat(), "downloads": history.get(day)} for day in _dates(start, end)
        ],
    }


def weekly(history: History) -> list[dict]:
    """ISO calendar weeks, Monday through Sunday."""
    return [
        {"week": "{}-W{:02d}".format(*start.isocalendar()[:2]), **tally(history, start, end)}
        for start, end in _spans(history, _week_span)
    ]


def monthly(history: History) -> list[dict]:
    return [
        {"month": f"{start:%Y-%m}", **tally(history, start, end)}
        for start, end in _spans(history, _month_span)
    ]


def yearly(history: History) -> list[dict]:
    return [
        {"year": start.year, **tally(history, start, end)}
        for start, end in _spans(history, _year_span)
    ]


def previous_window(history: History, days: int) -> dict | None:
    """Tally the `days` days just before the rolling window, for comparing the two."""
    if not history:
        return None
    end = max(history) - _ONE_DAY * days
    return tally(history, end - _ONE_DAY * (days - 1), end)


def find_gaps(history: History) -> list[dict]:
    """Runs of unrecorded days between the earliest and latest observations."""
    days = sorted(history)
    return [
        {
            "start": (before + _ONE_DAY).isoformat(),
            "end": (after - _ONE_DAY).isoformat(),
            "days": (after - before).days - 1,
        }
        for before, after in zip(days, days[1:])
        if after - before > _ONE_DAY
    ]


def summarize(history: History) -> dict:
    """Overall coverage. Totals are "recorded": history may not reach back to a package's start."""
    gaps = find_gaps(history)
    return {
        "earliest_date": min(history).isoformat() if history else None,
        "latest_date": max(history).isoformat() if history else None,
        "recorded_downloads": sum(history.values()),
        "recorded_days": len(history),
        "missing_days": sum(gap["days"] for gap in gaps),
        "gaps": gaps,
    }


def _dates(start: date, end: date) -> list[date]:
    return [start + _ONE_DAY * offset for offset in range((end - start).days + 1)]


def _spans(history: History, span: Callable[[date], tuple[date, date]]) -> Iterator[tuple[date, date]]:
    """Every period from the earliest to latest observation, including periods with no data."""
    if not history:
        return
    day, last = min(history), max(history)
    while day <= last:
        start, end = span(day)
        yield start, end
        day = end + _ONE_DAY


def _week_span(day: date) -> tuple[date, date]:
    monday = day - _ONE_DAY * day.weekday()
    return monday, monday + _ONE_DAY * 6


def _month_span(day: date) -> tuple[date, date]:
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _year_span(day: date) -> tuple[date, date]:
    return date(day.year, 1, 1), date(day.year, 12, 31)
