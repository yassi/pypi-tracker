"""Exceptions raised by pypi-tracker."""


class TrackerError(Exception):
    """Base class for every expected pypi-tracker failure."""


class ConfigError(TrackerError):
    """The configuration or a package name is invalid."""


class PyPIStatsError(TrackerError):
    """PyPIStats could not provide usable data for a package."""


class HistoryError(TrackerError):
    """A historical CSV file is missing or invalid."""


class PartialFailure(TrackerError):
    """One or more packages failed; the others were still processed."""

    def __init__(self, action: str, failures: dict[str, str]):
        self.failures = failures
        details = "\n".join(f"  {package}: {reason}" for package, reason in failures.items())
        super().__init__(f"{action} failed for {len(failures)} package(s):\n{details}")
