import json
import socket
from datetime import UTC, date, datetime, timedelta
from urllib.error import HTTPError, URLError

import pytest
from conftest import days

from pypi_tracker import ConfigError, HistoryError, PartialFailure, ReportSettings, Tracker
from pypi_tracker.reports import badge, compact_number
from pypi_tracker.storage import write_history

OLD = {"2020-01-01": 1, "2020-01-02": 2}


def tree(root):
    """Every file under a directory with its contents, for comparing whole outputs."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def tracker(tmp_path):
    return Tracker(["foo"], data_dir=tmp_path / "data", output_dir=tmp_path / "public")


class TestCollect:
    def test_initial_backfill_creates_missing_directories(self, tracker, api):
        api.responses["foo"] = OLD
        assert tracker.collect() == ["foo"]
        assert (tracker.data_dir / "foo.csv").read_text() == (
            "date,downloads\n2020-01-01,1\n2020-01-02,2\n"
        )

    def test_incremental_merge_keeps_dates_upstream_has_dropped(self, tracker, api):
        api.responses["foo"] = OLD
        tracker.collect()
        api.responses["foo"] = {"2020-07-01": 9}  # the earlier days aged out of the API window
        tracker.collect()
        assert tracker.history("foo") == {
            date(2020, 1, 1): 1,
            date(2020, 1, 2): 2,
            date(2020, 7, 1): 9,
        }

    def test_retention_beyond_upstream_window(self, tracker, api):
        start = date(2020, 1, 1)
        for offset in range(0, 720, 30):
            window = [start + timedelta(days=offset + n) for n in range(180)]
            api.responses["foo"] = {day.isoformat(): 1 for day in window}
            tracker.collect()
        assert len(tracker.history("foo")) == 870
        assert tracker.validate()["foo"]["gaps"] == []

    def test_overlap_and_corrections_take_the_upstream_value(self, tracker, api):
        api.responses["foo"] = OLD
        tracker.collect()
        api.responses["foo"] = {"2020-01-02": 50, "2020-01-03": 0}
        tracker.collect()
        assert tracker.history("foo") == {
            date(2020, 1, 1): 1,
            date(2020, 1, 2): 50,
            date(2020, 1, 3): 0,
        }

    def test_idempotent_collection_does_not_rewrite(self, tracker, api):
        api.responses["foo"] = OLD
        tracker.collect()
        csv = tracker.data_dir / "foo.csv"
        before = csv.stat().st_mtime_ns
        assert tracker.collect() == []
        assert csv.stat().st_mtime_ns == before

    def test_current_utc_day_is_not_stored(self, tracker, api):
        today = datetime.now(UTC).date()
        yesterday = today - timedelta(days=1)
        api.responses["foo"] = {yesterday.isoformat(): 5, today.isoformat(): 1}
        tracker.collect()
        assert tracker.history("foo") == {yesterday: 5}

    def test_failure_preserves_existing_data_and_other_packages_continue(self, tmp_path, api, sleeps):
        tracker = Tracker(["bad", "good"], data_dir=tmp_path)
        write_history(tmp_path / "bad.csv", {date(2020, 1, 1): 1})
        api.responses["bad"] = URLError("down")
        api.responses["good"] = OLD
        with pytest.raises(PartialFailure, match="bad: network error") as caught:
            tracker.collect()
        assert list(caught.value.failures) == ["bad"]
        assert tracker.history("bad") == {date(2020, 1, 1): 1}
        assert len(tracker.history("good")) == 2

    def test_missing_package_fails_without_creating_a_file(self, tracker, api):
        api.responses["foo"] = HTTPError("u", 404, "x", {}, None)
        with pytest.raises(PartialFailure, match="not found"):
            tracker.collect()
        assert not (tracker.data_dir / "foo.csv").exists()

    def test_corrupt_csv_is_never_overwritten(self, tracker, api):
        tracker.data_dir.mkdir()
        csv = tracker.data_dir / "foo.csv"
        csv.write_text("date,downloads\nnot-a-date,1\n")
        api.responses["foo"] = OLD
        with pytest.raises(PartialFailure, match="malformed date"):
            tracker.collect()
        assert csv.read_text() == "date,downloads\nnot-a-date,1\n"
        assert api.requests == []

    def test_no_packages_is_a_clear_error(self, tmp_path):
        with pytest.raises(ConfigError, match="no packages"):
            Tracker(data_dir=tmp_path).collect()

    def test_duplicate_package_names_cause_one_request(self, tmp_path, api):
        api.responses["foo-bar"] = OLD
        Tracker(["foo_bar", "Foo-Bar"], data_dir=tmp_path).collect()
        assert api.requests == ["foo-bar"]

    def test_removal_keeps_history_and_readding_resumes(self, tmp_path, api):
        config = tmp_path / "c.toml"
        config.write_text('packages = ["foo", "bar"]\n')
        api.responses["foo"] = OLD
        api.responses["bar"] = OLD
        Tracker.from_config(config, data_dir=tmp_path).collect()

        config.write_text('packages = ["foo"]\n')
        api.requests.clear()
        Tracker.from_config(config, data_dir=tmp_path).collect()
        assert api.requests == ["foo"]
        assert (tmp_path / "bar.csv").exists()

        config.write_text('packages = ["foo", "bar"]\n')
        api.responses["bar"] = {"2020-03-01": 7}
        tracker = Tracker.from_config(config, data_dir=tmp_path)
        tracker.collect()
        assert len(tracker.history("bar")) == 3


class TestBuild:
    @pytest.fixture
    def built(self, tracker):
        write_history(tracker.data_dir / "foo.csv", days("2025-12-30", 3))
        tracker.build()
        return tracker.output_dir / "packages" / "foo"

    def test_generates_the_documented_layout(self, built):
        assert sorted(tree(built)) == [
            "badges/last-180-days.json",
            "badges/last-30-days.json",
            "badges/last-7-days.json",
            "badges/last-90-days.json",
            "daily/2025.json",
            "daily/2026.json",
            "last-180-days.json",
            "last-30-days.json",
            "last-7-days.json",
            "last-90-days.json",
            "monthly.json",
            "summary.json",
            "weekly.json",
            "yearly.json",
        ]

    def test_rolling_report_shape(self, built):
        assert json.loads((built / "last-7-days.json").read_text()) == {
            "package": "foo",
            "report": "last-7-days",
            "as_of": "2026-01-01",
            "period": {"start": "2025-12-26", "end": "2026-01-01", "days": 7},
            "downloads": 30,
            "recorded_days": 3,
            "missing_days": 4,
            "complete": False,
            "previous": {
                "start": "2025-12-19",
                "end": "2025-12-25",
                "days": 7,
                "downloads": None,
                "recorded_days": 0,
                "missing_days": 7,
                "complete": False,
            },
            "observations": [
                {"date": "2025-12-26", "downloads": None},
                {"date": "2025-12-27", "downloads": None},
                {"date": "2025-12-28", "downloads": None},
                {"date": "2025-12-29", "downloads": None},
                {"date": "2025-12-30", "downloads": 10},
                {"date": "2025-12-31", "downloads": 10},
                {"date": "2026-01-01", "downloads": 10},
            ],
        }

    def test_each_rolling_report_has_one_point_per_day(self, built):
        for window in (7, 30, 90, 180):
            report = json.loads((built / f"last-{window}-days.json").read_text())
            assert len(report["observations"]) == window

    def test_daily_partitions_hold_only_their_year(self, built):
        assert json.loads((built / "daily/2026.json").read_text()) == {
            "package": "foo",
            "year": 2026,
            "observations": [{"date": "2026-01-01", "downloads": 10}],
        }
        assert len(json.loads((built / "daily/2025.json").read_text())["observations"]) == 2

    def test_summary_says_recorded_not_lifetime(self, built):
        summary = json.loads((built / "summary.json").read_text())
        assert summary["recorded_downloads"] == 30
        assert summary["as_of"] == "2026-01-01"
        assert "lifetime" not in json.dumps(summary)

    def test_calendar_report_periods(self, built):
        yearly = json.loads((built / "yearly.json").read_text())
        assert [period["year"] for period in yearly["periods"]] == [2025, 2026]
        assert all(period["complete"] is False for period in yearly["periods"])

    def test_rebuild_is_deterministic_and_rewrites_nothing(self, tracker, built):
        before = tree(built)
        times = {path: path.stat().st_mtime_ns for path in built.rglob("*.json")}
        assert tracker.build() == []
        assert tree(built) == before
        assert {path: path.stat().st_mtime_ns for path in built.rglob("*.json")} == times

    def test_correction_rewrites_only_affected_partition(self, tracker, built):
        history = tracker.history("foo")
        history[date(2025, 12, 30)] = 99
        write_history(tracker.data_dir / "foo.csv", history)
        names = [path.relative_to(built).as_posix() for path in tracker.build() if path.is_relative_to(built)]
        assert "daily/2025.json" in names
        assert "daily/2026.json" not in names

    def test_unrelated_output_files_are_preserved(self, tracker, built):
        other = tracker.output_dir / "index.html"
        other.write_text("hello")
        tracker.build()
        assert other.read_text() == "hello"

    def test_report_selection(self, tmp_path):
        tracker = Tracker(
            ["foo"],
            data_dir=tmp_path / "d",
            output_dir=tmp_path / "o",
            reports=ReportSettings(rolling_days=[14], calendar=["monthly"], historical=[], summary=False),
        )
        write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 1))
        tracker.build()
        assert sorted(tree(tmp_path / "o" / "packages" / "foo")) == ["badges/last-14-days.json", "last-14-days.json", "monthly.json"]

    def test_empty_history_produces_explicitly_unavailable_reports(self, tracker):
        write_history(tracker.data_dir / "foo.csv", {})
        tracker.build()
        root = tracker.output_dir / "packages" / "foo"
        window = json.loads((root / "last-7-days.json").read_text())
        assert (window["as_of"], window["downloads"], window["complete"]) == (None, None, False)

    def test_missing_csv_is_an_error_and_is_not_created(self, tracker):
        with pytest.raises(PartialFailure, match="file not found"):
            tracker.build()
        assert not (tracker.data_dir / "foo.csv").exists()

    def test_without_packages_builds_everything_stored(self, tmp_path):
        write_history(tmp_path / "d" / "a.csv", days("2026-01-01", 1))
        write_history(tmp_path / "d" / "b.csv", days("2026-01-01", 1))
        Tracker(data_dir=tmp_path / "d", output_dir=tmp_path / "o").build()
        assert sorted(path.name for path in (tmp_path / "o" / "packages").iterdir()) == ["a", "b"]

    def test_nothing_to_build_is_a_clear_error(self, tmp_path):
        with pytest.raises(ConfigError, match="no CSV files"):
            Tracker(data_dir=tmp_path).build()

    def test_makes_no_network_requests(self, tracker, built, api, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("network used")

        monkeypatch.setattr(socket, "socket", refuse)
        tracker.build()
        tracker.validate()
        assert api.requests == []


class TestSync:
    def test_collects_and_builds(self, tracker, api):
        api.responses["foo"] = OLD
        changed, written = tracker.sync()
        assert changed == ["foo"]
        assert tracker.output_dir / "packages" / "foo" / "summary.json" in written
        assert tracker.sync() == ([], [])

    def test_failure_raises_but_still_publishes_healthy_packages(self, tmp_path, api, sleeps):
        tracker = Tracker(["bad", "good"], data_dir=tmp_path / "d", output_dir=tmp_path / "o")
        api.responses["bad"] = URLError("down")
        api.responses["good"] = OLD
        with pytest.raises(PartialFailure, match="collection failed"):
            tracker.sync()
        assert (tmp_path / "o" / "packages" / "good" / "summary.json").exists()
        assert not (tmp_path / "o" / "packages" / "bad").exists()


def test_validate_separates_problems_from_gaps(tmp_path):
    (tmp_path / "bad.csv").write_text("date,downloads\n2026-01-01,-1\n")
    write_history(tmp_path / "gappy.csv", {date(2026, 1, 1): 1, date(2026, 1, 3): 1})
    results = Tracker(data_dir=tmp_path).validate()
    assert results["bad"]["problems"]
    assert results["gappy"]["problems"] == []
    assert results["gappy"]["gaps"] == [{"start": "2026-01-02", "end": "2026-01-02", "days": 1}]


def test_history_of_unknown_package_raises(tmp_path):
    with pytest.raises(HistoryError):
        Tracker(data_dir=tmp_path).history("nope")


class TestBadges:
    @pytest.mark.parametrize(
        ("number", "shown"),
        [(0, "0"), (999, "999"), (1000, "1.0k"), (1234, "1.2k"), (9960, "10.0k"), (38071, "38.1k"),
         (999_940, "999.9k"), (999_960, "1.0M"), (1_260_000, "1.3M"), (12_345_678_901_234, "12345.7B")],
    )
    def test_compact_number(self, number, shown):
        assert compact_number(number) == shown

    def test_periods_and_missing_data(self):
        assert badge(7, 1234) == {"schemaVersion": 1, "label": "downloads/week", "message": "1.2k", "color": "blue"}
        assert badge(30, 5)["label"] == "downloads/month"
        assert badge(90, 5)["label"] == "downloads/90 days"
        assert badge(30, None) == {"schemaVersion": 1, "label": "downloads/month", "message": "no data", "color": "lightgrey"}

    def test_written_for_packages_and_the_aggregate(self, tmp_path):
        tracker = Tracker(["foo", "bar"], data_dir=tmp_path / "d", output_dir=tmp_path / "o")
        write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 30, downloads=1000))
        write_history(tmp_path / "d" / "bar.csv", days("2026-01-01", 30, downloads=300))
        tracker.build()
        read = lambda path: json.loads((tmp_path / "o" / path).read_text())
        assert read("packages/foo/badges/last-30-days.json")["message"] == "30.0k"
        assert read("aggregate/badges/last-30-days.json")["message"] == "39.0k"
        assert read("aggregate/badges/last-7-days.json")["message"] == "9.1k"
