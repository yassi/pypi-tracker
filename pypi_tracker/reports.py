"""Shape aggregates into report payloads and publish them as static JSON files."""

import html
import json
from pathlib import Path

from . import aggregation
from .config import ReportSettings
from .errors import ConfigError
from .storage import History, write_atomic

_DASHBOARD_FILES = Path(__file__).parent / "dashboard"

_CALENDAR = {
    "weekly": aggregation.weekly,
    "monthly": aggregation.monthly,
    "yearly": aggregation.yearly,
}


def rolling_report(package: str, history: History, days: int) -> dict:
    window = aggregation.rolling_window(history, days)
    return {
        "package": package,
        "report": f"last-{days}-days",
        "as_of": _as_of(history),
        "period": {"start": window["start"], "end": window["end"], "days": days},
        "downloads": window["downloads"],
        "recorded_days": window["recorded_days"],
        "missing_days": window["missing_days"],
        "complete": window["complete"],
        "observations": window["observations"],
    }


def calendar_report(package: str, history: History, name: str) -> dict:
    """`name` is weekly, monthly or yearly."""
    return {
        "package": package,
        "report": name,
        "as_of": _as_of(history),
        "periods": _CALENDAR[name](history),
    }


def summary_report(package: str, history: History) -> dict:
    return {
        "package": package,
        "report": "summary",
        "as_of": _as_of(history),
        **aggregation.summarize(history),
    }


def daily_reports(package: str, history: History) -> dict[int, dict]:
    """One payload per calendar year that has observations."""
    years: dict[int, dict] = {}
    for day, downloads in sorted(history.items()):
        year = years.setdefault(
            day.year, {"package": package, "year": day.year, "observations": []}
        )
        year["observations"].append({"date": day.isoformat(), "downloads": downloads})
    return years


def write_reports(
    package: str, history: History, output_dir: Path, settings: ReportSettings
) -> list[Path]:
    """Write a package's selected reports and return the files that actually changed."""
    files: dict[str, str] = {}
    if settings.summary:
        files["summary.json"] = _readable(summary_report(package, history))
    for days in settings.rolling_days:
        files[f"last-{days}-days.json"] = _readable(rolling_report(package, history, days))
    for name in settings.calendar:
        files[f"{name}.json"] = _readable(calendar_report(package, history, name))
    if "daily" in settings.historical:
        for year, payload in daily_reports(package, history).items():
            files[f"daily/{year}.json"] = json.dumps(payload, separators=(",", ":")) + "\n"

    root = output_dir / "packages" / package
    return [root / name for name, text in files.items() if write_atomic(root / name, text)]


def write_dashboard(packages: list[str], output_dir: Path, settings: ReportSettings) -> list[Path]:
    """Write the static page that charts the reports and return the files that changed.

    It gets its own folder so it can never overwrite a user's site at the output root.
    """
    if not settings.rolling_days and not settings.calendar:
        raise ConfigError("nothing to chart: enable reports.rolling_days or reports.calendar")
    config = {
        "packages": packages,
        "rolling_days": sorted(set(settings.rolling_days)),
        "calendar": list(settings.calendar),
        "summary": settings.summary,
    }
    page = (_DASHBOARD_FILES / "index.html").read_text(encoding="utf-8")
    files = {
        "index.html": page.replace("__CONFIG__", html.escape(json.dumps(config))),
        "app.js": (_DASHBOARD_FILES / "app.js").read_text(encoding="utf-8"),
        "style.css": (_DASHBOARD_FILES / "style.css").read_text(encoding="utf-8"),
    }
    root = output_dir / "dashboard"
    return [root / name for name, text in files.items() if write_atomic(root / name, text)]


def _as_of(history: History) -> str | None:
    """The actual date of the newest data, so stale reports can be recognized as stale."""
    return max(history).isoformat() if history else None


def _readable(payload: dict) -> str:
    return json.dumps(payload, indent=2) + "\n"
