from datetime import date
from importlib.metadata import version
from urllib.error import URLError

import pytest
from conftest import days

from pypi_tracker.cli import main
from pypi_tracker.storage import write_history

OLD = {"2020-01-01": 1, "2020-01-02": 2}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A config in a subdirectory, with its own relative paths, run from an unrelated cwd."""
    config = tmp_path / "config" / "packages.toml"
    config.parent.mkdir()
    config.write_text('packages = ["foo"]\n[storage]\ndirectory = "d/"\n[output]\ndirectory = "o/"\n')
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return config


def test_config_in_the_current_directory_is_used_automatically(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pypi-tracker.toml").write_text('packages = ["foo"]\n[dashboard]\ntitle = "Mine"\n')
    write_history(tmp_path / "data" / "foo.csv", {date(2026, 1, 1): 1})
    assert main(["dashboard"]) == 0
    assert "<title>Mine</title>" in (tmp_path / "public" / "index.html").read_text()


def test_an_explicit_config_beats_the_one_in_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pypi-tracker.toml").write_text("not valid toml [")
    (tmp_path / "other.toml").write_text('packages = ["foo"]\n')
    write_history(tmp_path / "data" / "foo.csv", {date(2026, 1, 1): 1})
    assert main(["build", "--config", "other.toml"]) == 0


def test_version_flag_prints_the_installed_version(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out == f"pypi-tracker {version('pypi-tracker')}\n"


def test_collect_uses_paths_declared_relative_to_config(project, api):
    api.responses["foo"] = OLD
    assert main(["collect", "--config", str(project)]) == 0
    assert (project.parent / "d" / "foo.csv").exists()


def test_cli_data_dir_overrides_config_relative_to_cwd(project, api, tmp_path):
    api.responses["foo"] = OLD
    assert main(["collect", "--config", str(project), "--data-dir", "mine"]) == 0
    assert (tmp_path / "cwd" / "mine" / "foo.csv").exists()
    assert not (project.parent / "d").exists()


def test_collect_without_config_using_repeated_package(tmp_path, api):
    api.responses["foo"] = OLD
    api.responses["bar"] = OLD
    data = tmp_path / "data"
    assert main(["collect", "--package", "foo", "--package", "bar", "--data-dir", str(data)]) == 0
    assert sorted(path.name for path in data.iterdir()) == ["bar.csv", "foo.csv"]


def test_package_flag_replaces_config_packages(project, api):
    api.responses["other"] = OLD
    assert main(["collect", "--config", str(project), "--package", "other"]) == 0
    assert api.requests == ["other"]


def test_build_and_sync_write_to_output_dir(project, api, tmp_path):
    api.responses["foo"] = OLD
    assert main(["sync", "--config", str(project), "--output-dir", "site"]) == 0
    assert (tmp_path / "cwd" / "site" / "packages" / "foo" / "summary.json").exists()

    assert main(["build", "--config", str(project)]) == 0
    assert (project.parent / "o" / "packages" / "foo" / "summary.json").exists()


def test_sync_failure_exits_nonzero_and_says_so(project, api, sleeps, capsys):
    api.responses["foo"] = URLError("down")
    assert main(["sync", "--config", str(project)]) == 1
    captured = capsys.readouterr()
    assert "collection failed" in captured.err
    assert "synced" not in captured.out


@pytest.mark.parametrize(
    "args",
    [
        ["collect"],
        ["build", "--data-dir", "does-not-exist"],
        ["collect", "--package", "../evil"],
        ["build", "--config", "does-not-exist.toml"],
    ],
)
def test_errors_exit_nonzero_with_a_message(args, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(args) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_unwritable_output_path_is_a_clean_error(tmp_path, capsys):
    write_history(tmp_path / "d" / "foo.csv", days("2026-01-01", 1))
    (tmp_path / "blocked").write_text("a file, not a directory")
    code = main(["build", "--data-dir", str(tmp_path / "d"), "--output-dir", str(tmp_path / "blocked")])
    assert code == 1
    assert "report generation failed" in capsys.readouterr().err


class TestValidate:
    def test_gaps_are_reported_but_succeed(self, tmp_path, capsys):
        write_history(tmp_path / "foo.csv", {date(2026, 1, 1): 1, date(2026, 1, 3): 1})
        assert main(["validate", "--data-dir", str(tmp_path)]) == 0
        assert "coverage gap: 2026-01-02 to 2026-01-02" in capsys.readouterr().out

    def test_invalid_data_fails(self, tmp_path, capsys):
        (tmp_path / "foo.csv").write_text("date,downloads\n2026-01-01,-1\n")
        assert main(["validate", "--data-dir", str(tmp_path)]) == 1
        assert "negative" in capsys.readouterr().out

    def test_configured_package_without_csv_fails(self, tmp_path, capsys):
        assert main(["validate", "--data-dir", str(tmp_path), "--package", "foo"]) == 1
        assert "file not found" in capsys.readouterr().out


class TestInit:
    def test_generates_a_loadable_config(self, tmp_path, api):
        path = tmp_path / "sub" / "pypi-tracker.toml"
        assert main(["init", "--output", str(path)]) == 0
        api.responses["example-package"] = OLD
        assert main(["collect", "--config", str(path)]) == 0
        assert (tmp_path / "sub" / "data" / "example-package.csv").exists()

    def test_refuses_to_overwrite(self, tmp_path, capsys):
        path = tmp_path / "pypi-tracker.toml"
        path.write_text("mine")
        assert main(["init", "--output", str(path)]) == 1
        assert path.read_text() == "mine"
