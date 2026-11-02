import os
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from conftest import days

from pypi_tracker import Tracker
from pypi_tracker.cli import main
from pypi_tracker.server import _Watcher, make_server
from pypi_tracker.storage import write_history


def touch_later(path):
    """Make a change visible even if it lands within the filesystem's timestamp resolution."""
    later = path.stat().st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(later, later))


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "pypi-tracker.toml"
    config.write_text('packages = ["foo"]\n[dashboard]\ntitle = "First"\n')
    write_history(tmp_path / "data" / "foo.csv", days("2026-01-01", 3))
    load = lambda: Tracker.from_config(config)
    tracker = load()
    tracker.dashboard()
    server = make_server(0, tracker.output_dir)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield _Watcher(server, load, tracker, config)
    server.shutdown()
    server.server_close()


def get(watcher, path):
    with urlopen(f"http://127.0.0.1:{watcher.server.server_address[1]}{path}") as response:
        return response.read().decode(), response.headers


def test_pages_get_the_reload_script_but_the_written_file_does_not(site):
    assert "/__reload" in get(site, "/")[0]
    assert "/__reload" in get(site, "/index.html?range=7d")[0]
    assert "/__reload" not in (site.tracker.output_dir / "index.html").read_text()


def test_serves_reports_and_assets_without_caching(site):
    text, headers = get(site, "/aggregate/last-7-days.json")
    assert '"report": "last-7-days"' in text
    assert headers["Cache-Control"] == "no-store"
    assert "function" in get(site, "/assets/app.js")[0]


def test_no_change_means_no_reload(site):
    site.check()
    assert get(site, "/__reload")[0] == "0"


def test_a_config_edit_regenerates_and_tells_pages_to_reload(site):
    site.config.write_text('packages = ["foo"]\n[dashboard]\ntitle = "Second"\n')
    touch_later(site.config)
    site.check()
    assert get(site, "/__reload")[0] == "1"
    assert "<title>Second</title>" in get(site, "/")[0]


def test_new_data_regenerates(site):
    csv = site.tracker.data_dir / "foo.csv"
    write_history(csv, days("2026-01-01", 4))
    touch_later(csv)
    site.check()
    assert get(site, "/__reload")[0] == "1"
    assert '"as_of": "2026-01-04"' in get(site, "/aggregate/last-7-days.json")[0]


def test_touching_a_file_without_changing_it_does_not_reload(site):
    touch_later(site.config)
    site.check()
    assert get(site, "/__reload")[0] == "0"


def test_a_broken_edit_keeps_serving_the_last_good_site_then_recovers(site, capsys):
    good = site.config.read_text()
    site.config.write_text("not valid toml [")
    touch_later(site.config)
    site.check()
    assert capsys.readouterr().err.startswith("error: ")
    assert get(site, "/__reload")[0] == "0"
    assert "<title>First</title>" in get(site, "/")[0]

    site.config.write_text(good.replace("First", "Fixed"))
    touch_later(site.config)
    site.check()
    assert "<title>Fixed</title>" in get(site, "/")[0]


def test_only_this_computer_can_connect(site):
    assert site.server.server_address[0] == "127.0.0.1"


def test_files_outside_the_site_are_not_served(site, tmp_path):
    (tmp_path / "secret.txt").write_text("x")
    with pytest.raises(HTTPError):
        get(site, "/../secret.txt")


def test_logs_name_the_missing_file_and_skip_successful_requests(site, capsys):
    get(site, "/__reload")
    with pytest.raises(HTTPError):
        get(site, "/missing.json")
    err = capsys.readouterr().err
    assert "/missing.json" in err and "404" in err
    assert "__reload" not in err


def test_cli_port_in_use_is_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_history(tmp_path / "data" / "foo.csv", days("2026-01-01", 1))
    busy = make_server(0, tmp_path)
    try:
        assert main(["serve", "--port", str(busy.server_address[1])]) == 1
    finally:
        busy.server_close()
    assert capsys.readouterr().err.startswith("error: ")
    assert (tmp_path / "public" / "index.html").exists()  # generated before serving


def test_cli_with_no_data_fails_without_serving(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["serve"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
