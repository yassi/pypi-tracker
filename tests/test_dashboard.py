import html
import json
import re

import pytest
from conftest import days

from pypi_tracker import ConfigError, PartialFailure, ReportSettings, Tracker
from pypi_tracker.cli import main
from pypi_tracker.storage import write_history


def page_config(tracker):
    """The configuration embedded in the generated page, decoded as the browser would."""
    page = (tracker.output_dir / "dashboard" / "index.html").read_text()
    return json.loads(html.unescape(re.search(r'data-config="([^"]*)"', page).group(1)))


@pytest.fixture
def tracker(tmp_path):
    tracker = Tracker(["foo", "bar"], data_dir=tmp_path / "d", output_dir=tmp_path / "o")
    write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 3))
    write_history(tmp_path / "d" / "bar.csv", days("2026-01-01", 3))
    return tracker


def test_writes_only_html_javascript_and_css(tracker):
    tracker.dashboard()
    files = sorted(path.name for path in (tracker.output_dir / "dashboard").iterdir())
    assert files == ["app.js", "index.html", "style.css"]


def test_page_is_self_contained(tracker):
    tracker.dashboard()
    page = (tracker.output_dir / "dashboard" / "index.html").read_text()
    assert 'src="app.js"' in page and 'href="style.css"' in page
    assert "://" not in page  # no CDN: it must work with just these files


def test_also_builds_the_reports_it_charts(tracker):
    tracker.dashboard()
    assert (tracker.output_dir / "packages" / "foo" / "last-30-days.json").exists()
    assert (tracker.output_dir / "packages" / "bar" / "monthly.json").exists()


def test_page_config_lists_packages_and_selected_reports(tmp_path):
    tracker = Tracker(
        ["foo"],
        data_dir=tmp_path / "d",
        output_dir=tmp_path / "o",
        reports=ReportSettings(rolling_days=[30, 7, 30], calendar=["monthly"], summary=False),
    )
    write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 1))
    tracker.dashboard()
    assert page_config(tracker) == {
        "packages": ["foo"],
        "rolling_days": [7, 30],
        "calendar": ["monthly"],
        "summary": False,
    }


def test_without_a_package_list_it_charts_everything_stored(tmp_path):
    write_history(tmp_path / "d" / "b.csv", days("2026-01-01", 1))
    write_history(tmp_path / "d" / "a.csv", days("2026-01-01", 1))
    tracker = Tracker(data_dir=tmp_path / "d", output_dir=tmp_path / "o")
    tracker.dashboard()
    assert page_config(tracker)["packages"] == ["a", "b"]


def test_csv_file_names_cannot_inject_markup(tmp_path):
    # Names from the data directory aren't validated, so they must be escaped in the page.
    name = '"><script>alert(1)<'
    write_history(tmp_path / "d" / f"{name}.csv", days("2026-01-01", 1))
    tracker = Tracker(data_dir=tmp_path / "d", output_dir=tmp_path / "o")
    tracker.dashboard()
    assert "<script>alert" not in (tracker.output_dir / "dashboard" / "index.html").read_text()
    assert page_config(tracker)["packages"] == [name]


def test_rerun_rewrites_nothing(tracker):
    assert tracker.dashboard()
    assert tracker.dashboard() == []


def test_never_touches_a_users_own_page(tracker):
    tracker.output_dir.mkdir()
    (tracker.output_dir / "index.html").write_text("mine")
    tracker.dashboard()
    assert (tracker.output_dir / "index.html").read_text() == "mine"


def test_nothing_to_chart_is_a_clear_error(tmp_path):
    tracker = Tracker(
        ["foo"],
        data_dir=tmp_path / "d",
        output_dir=tmp_path / "o",
        reports=ReportSettings(rolling_days=[], calendar=[]),
    )
    write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 1))
    with pytest.raises(ConfigError, match="nothing to chart"):
        tracker.dashboard()


def test_missing_history_fails_and_writes_no_page(tmp_path):
    tracker = Tracker(["foo"], data_dir=tmp_path / "d", output_dir=tmp_path / "o")
    with pytest.raises(PartialFailure, match="file not found"):
        tracker.dashboard()
    assert not (tracker.output_dir / "dashboard").exists()


def test_makes_no_network_requests(tracker, api):
    tracker.dashboard()
    assert api.requests == []


def test_cli_uses_output_dir_and_explains_how_to_preview(tmp_path, capsys):
    write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 1))
    code = main(["dashboard", "--data-dir", str(tmp_path / "d"), "--output-dir", str(tmp_path / "site")])
    assert code == 0
    assert (tmp_path / "site" / "dashboard" / "index.html").exists()
    out = capsys.readouterr().out
    assert "http.server" in out and "http://localhost:8000/dashboard/" in out


def test_cli_failure_exits_nonzero(tmp_path, capsys):
    assert main(["dashboard", "--data-dir", str(tmp_path / "empty")]) == 1
    assert capsys.readouterr().err.startswith("error: ")
