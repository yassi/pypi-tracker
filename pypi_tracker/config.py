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

# Names become file paths, so this also blocks path traversal.
_PACKAGE_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")


@dataclass(frozen=True)
class ReportSettings:
    """Which reports `build` generates. Every report is on by default."""

    rolling_days: Sequence[int] = (7, 30, 90, 180)
    calendar: Sequence[str] = CALENDAR_REPORTS
    historical: Sequence[str] = HISTORICAL_REPORTS
    summary: bool = True

    def __post_init__(self):
        _require_list("reports.rolling_days", self.rolling_days)
        for days in self.rolling_days:
            if type(days) is not int or days < 1:
                raise ConfigError(f"reports.rolling_days must be positive integers, got {days!r}")
        _require_choices("reports.calendar", self.calendar, CALENDAR_REPORTS)
        _require_choices("reports.historical", self.historical, HISTORICAL_REPORTS)
        if type(self.summary) is not bool:
            raise ConfigError(f"reports.summary must be true or false, got {self.summary!r}")


@dataclass(frozen=True)
class Config:
    packages: list[str]
    reports: ReportSettings
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
    _reject_unknown_keys("top level", raw, {"packages", "reports", "storage", "output"})

    packages = raw.get("packages") or []
    _require_list("packages", packages)

    reports = _section(raw, "reports", {"rolling_days", "calendar", "historical", "summary"})
    return Config(
        packages=normalize_packages(packages),
        reports=ReportSettings(**reports),
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
