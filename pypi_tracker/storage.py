"""Historical CSV files (the source of truth) and atomic file writes."""

import csv
import os
import re
from datetime import date
from pathlib import Path

from .errors import HistoryError

History = dict[date, int]

_HEADER = ["date", "downloads"]


def write_atomic(path: Path, text: str) -> bool:
    """Replace a file's contents in one step. Returns False if it already had this content."""
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def scan_csv(path: Path) -> tuple[History, list[str]]:
    """Read a history file, returning its valid observations and every problem found."""
    if not path.exists():
        return {}, ["file not found"]

    history: History = {}
    problems: list[str] = []
    previous: date | None = None
    with path.open(newline="", encoding="utf-8", errors="replace") as file:
        reader = csv.reader(file)
        try:
            header = next(reader, None)
            if header != _HEADER:
                problems.append(f"line 1: expected header date,downloads, got {header}")
            for row in reader:
                where = f"line {reader.line_num}"
                if len(row) != 2:
                    problems.append(f"{where}: expected 2 columns, got {row}")
                    continue
                day = _parse_date(row[0])
                if day is None:
                    problems.append(f"{where}: malformed date {row[0]!r}")
                    continue
                if not re.fullmatch(r"-?[0-9]+", row[1]):
                    problems.append(f"{where}: malformed download count {row[1]!r}")
                    continue
                downloads = int(row[1])
                if downloads < 0:
                    problems.append(f"{where}: negative download count {downloads}")
                    continue
                if day in history:
                    problems.append(f"{where}: duplicate observation for {day}")
                    continue
                if previous and day < previous:
                    problems.append(f"{where}: {day} is out of order (after {previous})")
                history[day] = downloads
                previous = day
        except csv.Error as error:
            problems.append(f"line {reader.line_num}: {error}")
    return history, problems


def read_history(path: Path) -> History:
    """Read a history file, refusing to return partial data from an invalid one."""
    history, problems = scan_csv(path)
    if problems:
        raise HistoryError(f"{path}: " + "; ".join(problems))
    return history


def write_history(path: Path, history: History) -> bool:
    """Write observations in date order. Returns False if the file was already up to date."""
    rows = "".join(f"{day.isoformat()},{downloads}\n" for day, downloads in sorted(history.items()))
    return write_atomic(path, "date,downloads\n" + rows)


def _parse_date(text: str) -> date | None:
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return None
    # fromisoformat also accepts forms such as 20260101; stored dates must be YYYY-MM-DD.
    return day if day.isoformat() == text else None
