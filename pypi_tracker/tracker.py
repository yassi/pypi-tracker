"""The public Python API: collect, build, sync, dashboard and validate."""

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from . import aggregation, reports
from .client import fetch_overall
from .config import (
    DEFAULT_DATA_DIR,
    DEFAULT_OUTPUT_DIR,
    DashboardSettings,
    ReportSettings,
    load_config,
    normalize_packages,
    resolve_path,
)
from .errors import ConfigError, PartialFailure, TrackerError
from .storage import History, read_history, scan_csv, write_history


class Tracker:
    def __init__(
        self,
        packages: Iterable[str] = (),
        data_dir: str | Path = DEFAULT_DATA_DIR,
        output_dir: str | Path = DEFAULT_OUTPUT_DIR,
        reports: ReportSettings = ReportSettings(),
        site: DashboardSettings = DashboardSettings(),
    ):
        self.packages = normalize_packages(packages)
        self.data_dir = resolve_path(data_dir)
        self.output_dir = resolve_path(output_dir)
        self.reports = reports
        self.site = site

    @classmethod
    def from_config(
        cls,
        path: str | Path,
        data_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        packages: Iterable[str] | None = None,
    ) -> Self:
        """Load a config file. Explicit arguments win over its values, which win over defaults."""
        config = load_config(path)
        return cls(
            packages=packages or config.packages,
            data_dir=data_dir or config.data_dir or DEFAULT_DATA_DIR,
            output_dir=output_dir or config.output_dir or DEFAULT_OUTPUT_DIR,
            reports=config.reports,
            site=config.dashboard,
        )

    def collect(self) -> list[str]:
        """Merge PyPIStats data into each package's CSV and return the packages that changed.

        Raises PartialFailure after trying every package if any of them failed.
        """
        if not self.packages:
            raise ConfigError("no packages to collect: use --package or list them in a config file")
        changed, failures = [], {}
        for package in self.packages:
            try:
                if self._collect_one(package):
                    changed.append(package)
            except (TrackerError, OSError) as error:
                failures[package] = str(error)
        if failures:
            raise PartialFailure("collection", failures)
        return changed

    def build(self, packages: Iterable[str] | None = None) -> list[Path]:
        """Generate JSON reports from local CSVs and return the files that changed.

        Never touches the network. Raises PartialFailure after trying every package.
        Collection-wide output is only written when building every package, with no failures.
        """
        written, failures, histories = [], {}, {}
        for package in self._selected() if packages is None else packages:
            try:
                histories[package] = self.history(package)
                written += reports.write_reports(
                    package, histories[package], self.output_dir, self.reports
                )
            except (TrackerError, OSError) as error:
                failures[package] = str(error)
        if failures:
            raise PartialFailure("report generation", failures)
        if packages is None:
            written += reports.write_aggregate(histories, self.output_dir, self.reports)
            if self.site.enabled:
                written += self._write_site()
        return written

    def sync(self) -> tuple[list[str], list[Path]]:
        """Collect, then build. Returns (changed packages, changed report files).

        If collection fails for a package, the others are still published, its existing reports
        are left alone (their `as_of` shows the data's age), and PartialFailure is raised.
        """
        try:
            changed = self.collect()
        except PartialFailure as failure:
            self.build(package for package in self.packages if package not in failure.failures)
            raise
        return changed, self.build()

    def dashboard(self) -> list[Path]:
        """Build the reports, then the static site that charts them. Returns the changed files."""
        return self.build() + self._write_site()

    def history(self, package: str) -> History:
        """Stored observations for a package as {date: downloads}."""
        return read_history(self._csv_path(package))

    def validate(self) -> dict[str, dict]:
        """Check each CSV. `problems` are invalid data; `gaps` are only missing coverage."""
        results = {}
        for package in self._selected():
            history, problems = scan_csv(self._csv_path(package))
            results[package] = {
                "problems": problems,
                "gaps": aggregation.find_gaps(history),
                "recorded_days": len(history),
            }
        return results

    def _write_site(self) -> list[Path]:
        return reports.write_site(self._selected(), self.output_dir, self.reports, self.site)

    def _collect_one(self, package: str) -> bool:
        path = self._csv_path(package)
        # Read first: a damaged CSV must stop us before anything can overwrite it.
        existing = read_history(path) if path.exists() else {}
        today = datetime.now(UTC).date()
        fetched = {day: count for day, count in fetch_overall(package).items() if day < today}
        # Upstream wins on overlap (corrections); dates it no longer returns are kept (retention).
        return write_history(path, existing | fetched)

    def _csv_path(self, package: str) -> Path:
        return self.data_dir / f"{package}.csv"

    def _selected(self) -> list[str]:
        """Configured packages, or else every package with history in the data directory."""
        packages = self.packages or sorted(path.stem for path in self.data_dir.glob("*.csv"))
        if not packages:
            raise ConfigError(f"no packages configured and no CSV files found in {self.data_dir}")
        return packages
