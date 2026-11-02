import html
import json
import re
from datetime import date

import pytest
from conftest import days

from pypi_tracker import ConfigError, DashboardSettings, PartialFailure, ReportSettings, Tracker
from pypi_tracker.aggregation import combine
from pypi_tracker.cli import main
from pypi_tracker.config import load_config
from pypi_tracker.storage import write_history


def page_text(tracker):
    return (tracker.output_dir / "index.html").read_text()


def page_config(tracker):
    """The configuration embedded in the generated page, decoded as the browser would."""
    return json.loads(html.unescape(re.search(r'data-config="([^"]*)"', page_text(tracker)).group(1)))


def aggregate(tracker, name):
    return json.loads((tracker.output_dir / "aggregate" / name).read_text())


def make_tracker(tmp_path, histories, **options):
    for name, history in histories.items():
        write_history(tmp_path / "d" / f"{name}.csv", history)
    return Tracker(list(histories), data_dir=tmp_path / "d", output_dir=tmp_path / "o", **options)


@pytest.fixture
def tracker(tmp_path):
    return make_tracker(tmp_path, {"foo": days("2026-01-01", 3), "bar": days("2026-01-01", 3, downloads=5)})


class TestAggregateReports:
    def test_mirrors_the_package_report_layout(self, tracker):
        tracker.build()
        names = sorted(p.relative_to(tracker.output_dir / "aggregate").as_posix() for p in (tracker.output_dir / "aggregate").rglob("*.json"))
        same = sorted(p.relative_to(tracker.output_dir / "packages/foo").as_posix() for p in (tracker.output_dir / "packages/foo").rglob("*.json"))
        assert names == same

    def test_sums_packages_and_names_them(self, tracker):
        tracker.build()
        report = aggregate(tracker, "last-7-days.json")
        assert report["packages"] == ["foo", "bar"]
        assert "package" not in report
        assert report["downloads"] == 45
        assert report["observations"][-1] == {"date": "2026-01-03", "downloads": 15}
        assert len(report["observations"]) == 7

    def test_a_date_missing_from_one_package_still_counts_the_others(self):
        foo = days("2026-01-01", 3)
        bar = days("2026-01-01", 3)
        del bar[date(2026, 1, 2)]
        assert combine([foo, bar]) == {date(2026, 1, 1): 20, date(2026, 1, 2): 10, date(2026, 1, 3): 20}

    def test_rolling_ends_at_the_latest_date_of_any_package(self, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 10), "stale": days("2026-01-01", 5)})
        tracker.build()
        report = aggregate(tracker, "last-7-days.json")
        assert report["as_of"] == "2026-01-10"
        assert report["period"]["end"] == "2026-01-10"
        assert (report["recorded_days"], report["missing_days"], report["complete"]) == (7, 0, True)
        assert report["observations"][0] == {"date": "2026-01-04", "downloads": 20}
        assert report["observations"][-1] == {"date": "2026-01-10", "downloads": 10}

    def test_calendar_counts_periods_from_before_a_new_package(self, tmp_path):
        tracker = make_tracker(tmp_path, {"old": days("2026-01-01", 90), "new": days("2026-03-15", 17)})
        tracker.build()
        january, february, march = aggregate(tracker, "monthly.json")["periods"]
        assert (january["month"], january["downloads"], january["complete"]) == ("2026-01", 310, True)
        assert (february["month"], february["downloads"]) == ("2026-02", 280)
        assert (march["month"], march["downloads"], march["complete"]) == ("2026-03", 480, True)
        assert aggregate(tracker, "summary.json")["first_observed_date"] == "2026-01-01"

    def test_packages_with_no_shared_date_still_combine(self, tmp_path):
        tracker = make_tracker(tmp_path, {"a": days("2026-01-01", 2), "b": days("2026-02-01", 2)})
        tracker.build()
        report = aggregate(tracker, "last-30-days.json")
        assert (report["as_of"], report["downloads"], report["recorded_days"]) == ("2026-02-02", 20, 2)
        assert [period["downloads"] for period in aggregate(tracker, "monthly.json")["periods"]] == [20, 20]

    def test_no_data_at_all_is_an_unavailable_report(self, tmp_path):
        tracker = make_tracker(tmp_path, {"a": {}, "b": {}})
        tracker.build()
        report = aggregate(tracker, "last-30-days.json")
        assert (report["as_of"], report["downloads"], report["complete"]) == (None, None, False)
        assert report["observations"] == [] and report["breakdown"] == []
        assert aggregate(tracker, "monthly.json")["periods"] == []

    def test_breakdown_has_each_packages_own_numbers(self, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 14), "bar": days("2026-01-08", 7, downloads=5)})
        tracker.build()
        foo, bar = aggregate(tracker, "last-7-days.json")["breakdown"]
        assert foo == {"package": "foo", "downloads": 70, "recorded_days": 7, "missing_days": 0, "previous_downloads": 70}
        assert bar == {"package": "bar", "downloads": 35, "recorded_days": 7, "missing_days": 0, "previous_downloads": None}

    def test_previous_window_is_the_days_just_before(self, tracker, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 14)})
        tracker.build()
        previous = aggregate(tracker, "last-7-days.json")["previous"]
        assert (previous["start"], previous["end"], previous["downloads"], previous["complete"]) == ("2026-01-01", "2026-01-07", 70, True)

    def test_a_failing_package_leaves_the_aggregate_alone(self, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 3)})
        tracker.packages = ["foo", "missing"]
        with pytest.raises(PartialFailure):
            tracker.build()
        assert not (tracker.output_dir / "aggregate").exists()

    def test_makes_no_network_requests(self, tracker, api):
        tracker.dashboard()
        assert api.requests == []


class TestSite:
    def test_layout(self, tracker):
        tracker.dashboard()
        assert (tracker.output_dir / "index.html").exists()
        assert sorted(p.name for p in (tracker.output_dir / "assets").iterdir()) == ["app.js", "style.css"]
        assert (tracker.output_dir / "aggregate" / "last-30-days.json").exists()
        assert (tracker.output_dir / "packages" / "foo" / "monthly.json").exists()

    def test_looks_finished_with_no_branding(self, tracker):
        tracker.dashboard()
        page = page_text(tracker)
        assert "<title>PyPI Package Statistics</title>" in page
        assert '<span class="monogram" aria-hidden="true">P</span>' in page
        assert 'name="description"' not in page
        assert "__" not in page

    def test_loads_nothing_from_other_sites(self, tracker):
        tracker.dashboard()
        assert not re.search(r'<(script|link)[^>]+(src|href)="https?:', page_text(tracker))

    def test_branding(self, tmp_path):
        logo = tmp_path / "Logo.PNG"
        logo.write_bytes(b"\x89PNG\xff")
        tracker = make_tracker(
            tmp_path,
            {"foo": days("2026-01-01", 1)},
            site=DashboardSettings(title="My <Stats>", description="All & more", logo=logo, theme="dark", accent_color="#6366f1"),
        )
        tracker.dashboard()
        page = page_text(tracker)
        assert "<title>My &lt;Stats&gt;</title>" in page
        assert '<meta name="description" content="All &amp; more">' in page
        assert "<p>All &amp; more</p>" in page
        assert '<html lang="en" data-theme="dark" style="--accent: #6366f1">' in page
        assert '<img class="logo" src="assets/logo.png" alt="">' in page
        assert '<link rel="icon" href="assets/logo.png">' in page
        assert (tracker.output_dir / "assets" / "logo.png").read_bytes() == b"\x89PNG\xff"

    def test_title_containing_a_placeholder_is_left_as_written(self, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 1)}, site=DashboardSettings(title="__CONFIG__"))
        tracker.dashboard()
        assert "<title>__CONFIG__</title>" in page_text(tracker)

    def test_missing_logo_is_a_clear_error(self, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 1)}, site=DashboardSettings(logo=tmp_path / "no.svg"))
        with pytest.raises(ConfigError, match="cannot read dashboard.logo"):
            tracker.dashboard()

    def test_page_config(self, tmp_path):
        tracker = make_tracker(
            tmp_path, {"foo": days("2026-01-01", 1)}, reports=ReportSettings(rolling_days=[30, 7, 30], calendar=[])
        )
        tracker.dashboard()
        assert page_config(tracker) == {"packages": ["foo"], "rolling_days": [7, 30]}

    def test_without_a_package_list_it_charts_everything_stored(self, tmp_path):
        write_history(tmp_path / "d" / "b.csv", days("2026-01-01", 1))
        write_history(tmp_path / "d" / "a.csv", days("2026-01-01", 1))
        tracker = Tracker(data_dir=tmp_path / "d", output_dir=tmp_path / "o")
        tracker.dashboard()
        assert page_config(tracker)["packages"] == ["a", "b"]

    def test_csv_file_names_cannot_inject_markup(self, tmp_path):
        # Names from the data directory aren't validated, so they must be escaped in the page.
        name = '"><script>alert(1)<'
        write_history(tmp_path / "d" / f"{name}.csv", days("2026-01-01", 1))
        tracker = Tracker(data_dir=tmp_path / "d", output_dir=tmp_path / "o")
        tracker.dashboard()
        assert "<script>alert" not in page_text(tracker)
        assert page_config(tracker)["packages"] == [name]

    def test_rerun_rewrites_nothing(self, tracker):
        assert tracker.dashboard()
        assert tracker.dashboard() == []

    def test_refuses_to_overwrite_a_page_it_did_not_generate(self, tracker):
        tracker.output_dir.mkdir()
        (tracker.output_dir / "index.html").write_text("mine")
        with pytest.raises(ConfigError, match="not overwriting"):
            tracker.dashboard()
        assert page_text(tracker) == "mine"

    def test_needs_the_reports_it_reads(self, tmp_path):
        tracker = make_tracker(tmp_path, {"foo": days("2026-01-01", 1)}, reports=ReportSettings(historical=[]))
        with pytest.raises(ConfigError, match="dashboard needs"):
            tracker.dashboard()

    def test_build_only_writes_the_site_when_enabled(self, tmp_path, tracker):
        tracker.build()
        assert not (tracker.output_dir / "index.html").exists()
        enabled = make_tracker(tmp_path, {"foo": days("2026-01-01", 1)}, site=DashboardSettings(enabled=True))
        enabled.build()
        assert (enabled.output_dir / "index.html").exists()

    def test_missing_history_fails_and_writes_no_page(self, tmp_path):
        tracker = Tracker(["foo"], data_dir=tmp_path / "d", output_dir=tmp_path / "o")
        with pytest.raises(PartialFailure, match="file not found"):
            tracker.dashboard()
        assert not (tracker.output_dir / "index.html").exists()


class TestConfig:
    def write(self, tmp_path, text):
        path = tmp_path / "c.toml"
        path.write_text(text)
        return path

    def test_logo_is_relative_to_the_config_file(self, tmp_path):
        config = load_config(self.write(tmp_path, '[dashboard]\nlogo = "assets/logo.svg"\ntitle = "T"\n'))
        assert config.dashboard.logo == tmp_path.resolve() / "assets" / "logo.svg"
        assert config.dashboard.title == "T"

    def test_everything_is_optional(self, tmp_path):
        assert load_config(self.write(tmp_path, "")).dashboard == DashboardSettings()

    @pytest.mark.parametrize(
        "line",
        [
            'theme = "pink"',
            'accent_color = "red; background: url(x)"',
            "accent_color = 5",
            'logo = "logo.gif"',
            "title = 5",
            'enabled = "yes"',
            "unknown = 1",
        ],
    )
    def test_rejects_invalid_settings(self, tmp_path, line):
        with pytest.raises(ConfigError):
            load_config(self.write(tmp_path, f"[dashboard]\n{line}\n"))


def test_cli_uses_output_dir_and_explains_how_to_preview(tmp_path, capsys):
    write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 1))
    code = main(["dashboard", "--data-dir", str(tmp_path / "d"), "--output-dir", str(tmp_path / "site")])
    assert code == 0
    assert (tmp_path / "site" / "index.html").exists()
    out = capsys.readouterr().out
    assert "pypi-tracker serve" in out


def test_cli_failure_exits_nonzero(tmp_path, capsys):
    assert main(["dashboard", "--data-dir", str(tmp_path / "empty")]) == 1
    assert capsys.readouterr().err.startswith("error: ")
