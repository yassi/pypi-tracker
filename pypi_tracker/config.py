"""Load and validate the TOML configuration file."""

import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

DEFAULT_DATA_DIR = "data"
DEFAULT_OUTPUT_DIR = "public"
CALENDAR_REPORTS = ("weekly", "monthly", "yearly")
HISTORICAL_REPORTS = ("daily",)
THEMES = ("auto", "light", "dark")
LOGO_TYPES = (".svg", ".png", ".webp")

# Names become file paths, so this also blocks path traversal.
_PACKAGE_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")


@dataclass(frozen=True)
class ReportSettings:
    """Which reports `build` generates. Every report is on by default."""

    rolling_days: Sequence[int] = (7, 30, 90, 180)
    calendar: Sequence[str] = CALENDAR_REPORTS
    historical: Sequence[str] = HISTORICAL_REPORTS
    summary: bool = True
    aggregate: bool = True

    def __post_init__(self):
        _require_list("reports.rolling_days", self.rolling_days)
        for days in self.rolling_days:
            if type(days) is not int or days < 1:
                raise ConfigError(f"reports.rolling_days must be positive integers, got {days!r}")
        _require_choices("reports.calendar", self.calendar, CALENDAR_REPORTS)
        _require_choices("reports.historical", self.historical, HISTORICAL_REPORTS)
        for name in ("summary", "aggregate"):
            if type(getattr(self, name)) is not bool:
                raise ConfigError(f"reports.{name} must be true or false, got {getattr(self, name)!r}")


@dataclass(frozen=True)
class DashboardSettings:
    """The generated site's identity. `enabled` makes `build` and `sync` write the site too."""

    enabled: bool = False
    title: str = "PyPI Package Statistics"
    description: str = ""
    logo: str | Path | None = None
    theme: str = "auto"
    accent_color: str | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ConfigError(f"dashboard.enabled must be true or false, got {self.enabled!r}")
        for name in ("title", "description"):
            if not isinstance(getattr(self, name), str):
                raise ConfigError(f"dashboard.{name} must be text, got {getattr(self, name)!r}")
        if self.logo is not None and Path(self.logo).suffix.lower() not in LOGO_TYPES:
            raise ConfigError(f"dashboard.logo must be one of {', '.join(LOGO_TYPES)}, got {self.logo!r}")
        if self.theme not in THEMES:
            raise ConfigError(f"dashboard.theme must be one of {', '.join(THEMES)}, got {self.theme!r}")
        # This value goes straight into the page's CSS, so only real colors are allowed.
        if self.accent_color is not None and not (
            isinstance(self.accent_color, str)
            and re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", self.accent_color)
        ):
            raise ConfigError(f"dashboard.accent_color must look like #6366f1, got {self.accent_color!r}")


@dataclass(frozen=True)
class Config:
    packages: list[str]
    reports: ReportSettings
    dashboard: DashboardSettings
    data_dir: Path | None
    output_dir: Path | None


def normalize_packages(names: Iterable[str]) -> list[str]:
    """Validate names and apply PEP 503 normalization, so `Foo_Bar` and `foo-bar` share one CSV."""
    packages = []
    for name in names:
        if not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name):
            raise ConfigError(f"invalid package name: {name!r}")
        packages.append(re.sub(r"[-_.]+", "-", name).lower())
    return list(dict.fromkeys(packages))


def resolve_path(value: str | Path, base: Path | None = None) -> Path:
    """Expand `~` and make a path absolute. Relative paths start at `base` (default: the cwd)."""
    return ((base or Path.cwd()) / Path(value).expanduser()).resolve()


def load_config(path: str | Path) -> Config:
    path = resolve_path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read config file {path}: {error}") from error
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error
    try:
        return _parse(raw, base=path.parent)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from error


def _parse(raw: dict, base: Path) -> Config:
    _reject_unknown_keys("top level", raw, {"packages", "reports", "dashboard", "storage", "output"})

    packages = raw.get("packages") or []
    _require_list("packages", packages)

    reports = _section(raw, "reports", {"rolling_days", "calendar", "historical", "summary", "aggregate"})
    dashboard = _section(
        raw, "dashboard", {"enabled", "title", "description", "logo", "theme", "accent_color"}
    )
    if isinstance(dashboard.get("logo"), str):
        dashboard["logo"] = resolve_path(dashboard["logo"], base)
    return Config(
        packages=normalize_packages(packages),
        reports=ReportSettings(**reports),
        dashboard=DashboardSettings(**dashboard),
        data_dir=_directory(raw, "storage", base),
        output_dir=_directory(raw, "output", base),
    )


def _directory(raw: dict, key: str, base: Path) -> Path | None:
    directory = _section(raw, key, {"directory"}).get("directory")
    if directory is None:
        return None
    if not isinstance(directory, str):
        raise ConfigError(f"{key}.directory must be a path, got {directory!r}")
    return resolve_path(directory, base)


def _section(raw: dict, key: str, allowed: set[str]) -> dict:
    section = raw.get(key) or {}
    if not isinstance(section, dict):
        raise ConfigError(f"'{key}' must be a mapping")
    _reject_unknown_keys(key, section, allowed)
    return section


def _reject_unknown_keys(where: str, mapping: dict, allowed: set[str]) -> None:
    unknown = sorted(str(key) for key in set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"unknown key(s) in {where}: {', '.join(unknown)}")


def _require_list(name: str, value: object) -> None:
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"{name} must be a list, got {value!r}")


def _require_choices(name: str, value: object, choices: tuple[str, ...]) -> None:
    _require_list(name, value)
    for item in value:
        if item not in choices:
            raise ConfigError(f"{name}: unknown report {item!r}; choose from {', '.join(choices)}")
