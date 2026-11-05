"""Shape aggregates into report payloads and publish them as static JSON files and a site."""

import html
import json
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from . import aggregation
from .config import DashboardSettings, ReportSettings
from .errors import ConfigError
from .storage import History, write_atomic

_SITE_FILES = Path(__file__).parent / "dashboard"
_GENERATOR_TAG = '<meta name="generator" content="pypi-tracker">'
_DEFAULT_ICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
    "%3Crect width='16' height='16' rx='4' fill='%232a78d6'/%3E"
    "%3Cpath d='M3 11l3-3 3 2 4-5' fill='none' stroke='white' stroke-width='1.5'/%3E%3C/svg%3E"
)

_BADGE_PERIODS = {7: "week", 30: "month", 365: "year"}

_CALENDAR = {
    "weekly": aggregation.weekly,
    "monthly": aggregation.monthly,
    "yearly": aggregation.yearly,
}


def _subject(who: str | list[str]) -> dict:
    """Reports describe one package, or (given a list) the collection of packages."""
    return {"packages": who} if isinstance(who, list) else {"package": who}


def rolling_report(who: str | list[str], history: History, days: int) -> dict:
    window = aggregation.rolling_window(history, days)
    return {
        **_subject(who),
        "report": f"last-{days}-days",
        "as_of": _as_of(history),
        "period": {"start": window["start"], "end": window["end"], "days": days},
        "downloads": window["downloads"],
        "recorded_days": window["recorded_days"],
        "missing_days": window["missing_days"],
        "complete": window["complete"],
        "previous": aggregation.previous_window(history, days),
        "observations": window["observations"],
    }


def calendar_report(who: str | list[str], history: History, name: str) -> dict:
    """`name` is weekly, monthly or yearly."""
    return {
        **_subject(who),
        "report": name,
        "as_of": _as_of(history),
        "periods": _CALENDAR[name](history),
    }


def summary_report(who: str | list[str], history: History) -> dict:
    return {
        **_subject(who),
        "report": "summary",
        "as_of": _as_of(history),
        **aggregation.summarize(history),
    }


def badge(days: int, downloads: int | None) -> dict:
    """A shields.io endpoint badge for a rolling window: https://shields.io/badges/endpoint-badge"""
    period = _BADGE_PERIODS.get(days, f"{days} days")
    if downloads is None:
        return {"schemaVersion": 1, "label": f"downloads/{period}", "message": "no data", "color": "lightgrey"}
    return {"schemaVersion": 1, "label": f"downloads/{period}", "message": compact_number(downloads), "color": "blue"}


def compact_number(number: int) -> str:
    """950, 1.2k, 38.1k, 1.0M: one decimal place once shortened, rounding up into the next unit when needed."""
    value = float(number)
    for suffix in ("", "k", "M", "B"):
        shown = f"{value:.1f}" if suffix else f"{value:.0f}"
        if float(shown) < 1000 or suffix == "B":
            return shown + suffix
        value /= 1000
    raise AssertionError("unreachable")


def daily_reports(who: str | list[str], history: History) -> dict[int, dict]:
    """One payload per calendar year that has observations."""
    years: dict[int, dict] = {}
    for day, downloads in sorted(history.items()):
        year = years.setdefault(day.year, {**_subject(who), "year": day.year, "observations": []})
        year["observations"].append({"date": day.isoformat(), "downloads": downloads})
    return years


def write_reports(
    package: str, history: History, output_dir: Path, settings: ReportSettings
) -> list[Path]:
    """Write a package's selected reports and return the files that actually changed."""
    payloads = _payloads(package, history, settings)
    return _write_payloads(payloads, output_dir / "packages" / package)


def write_aggregate(
    histories: Mapping[str, History], output_dir: Path, settings: ReportSettings
) -> list[Path]:
    """Write the same reports for the whole collection and return the files that changed.

    A date counts when any package has it, so reports end at the latest date of any package.
    """
    payloads = _payloads(list(histories), aggregation.combine(histories.values()), settings)

    for days in settings.rolling_days:
        payload = payloads[f"last-{days}-days.json"]
        payload["breakdown"] = _breakdown(histories, payload["period"], payload["previous"])
    if settings.summary:
        payloads["summary.json"]["first_observed_date"] = payloads["summary.json"]["earliest_date"]
    return _write_payloads(payloads, output_dir / "aggregate")


def write_site(
    packages: list[str], output_dir: Path, reports: ReportSettings, site: DashboardSettings
) -> list[Path]:
    """Write the static site that charts the reports and return the files that changed."""
    if "daily" not in reports.historical or not reports.summary:
        raise ConfigError('the dashboard needs reports.historical = ["daily"] and reports.summary = true')

    page_path = output_dir / "index.html"
    if page_path.exists() and _GENERATOR_TAG not in page_path.read_text(encoding="utf-8", errors="replace"):
        raise ConfigError(f"{page_path} already exists and wasn't made by pypi-tracker; not overwriting it")

    files: dict[str, str | bytes] = {
        f"assets/{name}": (_SITE_FILES / name).read_text(encoding="utf-8")
        for name in ("app.js", "style.css")
    }
    logo_url = None
    if site.logo is not None:
        logo = Path(site.logo)
        try:
            logo_url = f"assets/logo{logo.suffix.lower()}"
            files[logo_url] = logo.read_bytes()
        except OSError as error:
            raise ConfigError(f"cannot read dashboard.logo {logo}: {error}") from error
    files["index.html"] = _render_page(packages, reports, site, logo_url)
    return [output_dir / name for name, content in files.items() if write_atomic(output_dir / name, content)]


def _render_page(
    packages: list[str], reports: ReportSettings, site: DashboardSettings, logo_url: str | None
) -> str:
    title = html.escape(site.title)
    description = html.escape(site.description)
    config = {"packages": packages, "rolling_days": sorted(set(reports.rolling_days)), "calendar": list(reports.calendar)}
    values = {
        "THEME": "" if site.theme == "auto" else f'data-theme="{site.theme}"',
        "ACCENT": f'style="--accent: {site.accent_color}"' if site.accent_color else "",
        "GENERATOR": _GENERATOR_TAG,
        "TITLE": title,
        "META": f'<meta name="description" content="{description}">' if description else "",
        "ICON": logo_url or _DEFAULT_ICON,
        "CONFIG": html.escape(json.dumps(config)),
        "IDENTITY": (
            f'<img class="logo" src="{logo_url}" alt="">'
            if logo_url
            else f'<span class="monogram" aria-hidden="true">{html.escape(site.title[:1].upper())}</span>'
        ),
        "DESCRIPTION": f"<p>{description}</p>" if description else "",
    }
    page = (_SITE_FILES / "index.html").read_text(encoding="utf-8")
    # One pass, so a title containing a placeholder's name can't be replaced a second time.
    return re.sub(r"__([A-Z]+)__", lambda match: values[match[1]], page)


def _payloads(who: str | list[str], history: History, settings: ReportSettings) -> dict[str, dict]:
    payloads: dict[str, dict] = {}
    if settings.summary:
        payloads["summary.json"] = summary_report(who, history)
    for days in settings.rolling_days:
        payloads[f"last-{days}-days.json"] = rolling_report(who, history, days)
        payloads[f"badges/last-{days}-days.json"] = badge(days, payloads[f"last-{days}-days.json"]["downloads"])
    for name in settings.calendar:
        payloads[f"{name}.json"] = calendar_report(who, history, name)
    if "daily" in settings.historical:
        for year, payload in daily_reports(who, history).items():
            payloads[f"daily/{year}.json"] = payload
    return payloads


def _write_payloads(payloads: dict[str, dict], root: Path) -> list[Path]:
    written = []
    for name, payload in payloads.items():
        # Daily files are read by programs and can get long, so they skip the indentation.
        indent = None if name.startswith("daily/") else 2
        separators = (",", ":") if indent is None else None
        text = json.dumps(payload, indent=indent, separators=separators) + "\n"
        if write_atomic(root / name, text):
            written.append(root / name)
    return written


def _breakdown(histories: Mapping[str, History], period: dict, previous: dict | None) -> list[dict]:
    """Each package's own numbers for the window, so a page can show them without more requests."""
    if period["start"] is None:
        return []
    rows = []
    for package, history in histories.items():
        now = aggregation.tally(history, date.fromisoformat(period["start"]), date.fromisoformat(period["end"]))
        before = aggregation.tally(history, date.fromisoformat(previous["start"]), date.fromisoformat(previous["end"]))
        rows.append({
            "package": package,
            "downloads": now["downloads"],
            "recorded_days": now["recorded_days"],
            "missing_days": now["missing_days"],
            "previous_downloads": before["downloads"] if before["complete"] else None,
        })
    return rows


def _as_of(history: History) -> str | None:
    """The actual date of the newest data, so stale reports can be recognized as stale."""
    return max(history).isoformat() if history else None
