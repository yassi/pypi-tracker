import os
from datetime import date

import pytest

from pypi_tracker import HistoryError
from pypi_tracker.storage import read_history, scan_csv, write_atomic, write_history


def test_round_trip_is_sorted_and_deterministic(tmp_path):
    path = tmp_path / "foo.csv"
    write_history(path, {date(2026, 1, 2): 0, date(2026, 1, 1): 1234})
    assert path.read_text() == "date,downloads\n2026-01-01,1234\n2026-01-02,0\n"
    assert read_history(path) == {date(2026, 1, 1): 1234, date(2026, 1, 2): 0}


def test_unchanged_data_does_not_rewrite(tmp_path):
    path = tmp_path / "foo.csv"
    assert write_history(path, {date(2026, 1, 1): 1}) is True
    before = path.stat().st_mtime_ns
    assert write_history(path, {date(2026, 1, 1): 1}) is False
    assert path.stat().st_mtime_ns == before


def test_failed_write_preserves_the_original_and_leaves_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "foo.txt"
    path.write_text("original")

    def explode(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError, match="disk full"):
        write_atomic(path, "new")
    assert path.read_text() == "original"
    assert os.listdir(tmp_path) == ["foo.txt"]


def test_written_files_are_readable_by_others(tmp_path):
    # A web server or CI step must be able to read these; mkstemp-style 0600 would break that.
    path = tmp_path / "foo.txt"
    previous = os.umask(0o022)
    try:
        write_atomic(path, "x")
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "expected header"),
        ("day,count\n2026-01-01,1\n", "expected header"),
        ("date,downloads\n2026-13-01,1\n", "malformed date"),
        ("date,downloads\n20260101,1\n", "malformed date"),
        ("date,downloads\n2026-01-01,-1\n", "negative"),
        ("date,downloads\n2026-01-01,1.5\n", "malformed download count"),
        ("date,downloads\n2026-01-01,\n", "malformed download count"),
        ("date,downloads\n2026-01-01\n", "expected 2 columns"),
        ("date,downloads\n2026-01-01,1\n2026-01-01,1\n", "duplicate"),
        ("date,downloads\n2026-01-02,1\n2026-01-01,1\n", "out of order"),
        ("date,downloads\n<<<<<<< HEAD\n", "expected 2 columns"),
    ],
)
def test_invalid_files_are_reported_and_refused(tmp_path, text, message):
    path = tmp_path / "foo.csv"
    path.write_text(text)
    assert message in " ".join(scan_csv(path)[1])
    with pytest.raises(HistoryError, match=message):
        read_history(path)


def test_scan_reports_every_problem_with_its_line(tmp_path):
    path = tmp_path / "foo.csv"
    path.write_text("date,downloads\nbad,1\n2026-01-01,-5\n2026-01-02,7\n")
    history, problems = scan_csv(path)
    assert history == {date(2026, 1, 2): 7}
    assert [problem.split(":")[0] for problem in problems] == ["line 2", "line 3"]


def test_missing_file_is_an_error_not_empty_history(tmp_path):
    with pytest.raises(HistoryError, match="file not found"):
        read_history(tmp_path / "nope.csv")
    assert not (tmp_path / "nope.csv").exists()
