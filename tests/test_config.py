import os
from pathlib import Path

import pytest

from pypi_tracker import Collection, ConfigError, ReportSettings, Tracker
from pypi_tracker.config import load_config, normalize_packages, resolve_path


def write(tmp_path, text):
    path = tmp_path / "config" / "packages.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def test_loads_a_full_config(tmp_path):
    path = write(
        tmp_path,
        """
packages = ["foo", "Bar_Baz"]

[reports]
rolling_days = [14]
calendar = ["monthly"]
historical = []
summary = false

[storage]
directory = "history/"

[output]
directory = "site/"
""",
    )
    config = load_config(path)
    assert config.packages == ["foo", "bar-baz"]
    assert config.reports == ReportSettings([14], ["monthly"], [], False)
    assert config.data_dir == (path.parent / "history").resolve()
    assert config.output_dir == (path.parent / "site").resolve()


def test_loads_collections(tmp_path):
    config = load_config(write(tmp_path, """
packages = ["loose"]

[collections.dcr]
title = "Django Control Room"
packages = ["dj-control-room", "DJ_Redis_Panel"]
aggregate = true

[collections.frameworks]
packages = ["django", "flask"]
"""))
    assert config.packages == ["loose"]
    assert config.collections == [
        Collection("dcr", ("dj-control-room", "dj-redis-panel"), "Django Control Room", True),
        Collection("frameworks", ("django", "flask")),
    ]


def test_collection_packages_are_tracked_once_each():
    tracker = Tracker(["a", "b"], collections=[Collection("one", ["b", "c"]), Collection("two", ["c", "d"])])
    assert tracker.packages == ["a", "b", "c", "d"]


def test_explicit_packages_replace_the_configs_collections(tmp_path):
    path = write(tmp_path, '[collections.dcr]\npackages = ["foo"]\naggregate = true\n')
    tracker = Tracker.from_config(path, packages=["bar"])
    assert (tracker.packages, tracker.collections) == (["bar"], [])


def test_omitted_report_keys_use_defaults(tmp_path):
    config = load_config(write(tmp_path, 'packages = ["foo"]\n[reports]\nsummary = false\n'))
    assert config.reports.rolling_days == (7, 30, 90, 180)
    assert config.reports.summary is False
    assert config.reports.aggregate is True


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('packages = "foo"\n', "packages must be a list"),
        ('packages = ["../evil"]\n', "invalid package name"),
        ("packages = [1]\n", "invalid package name"),
        ('packagez = ["foo"]\n', "unknown key"),
        ("[reports]\nrolling = [7]\n", "unknown key"),
        ("[reports]\nrolling_days = [0]\n", "positive integers"),
        ("[reports]\nrolling_days = [-7]\n", "positive integers"),
        ("[reports]\nrolling_days = [true]\n", "positive integers"),
        ("[reports]\nrolling_days = 7\n", "must be a list"),
        ('[reports]\ncalendar = ["hourly"]\n', "unknown report"),
        ('[reports]\nhistorical = ["weekly"]\n', "unknown report"),
        ('[reports]\nsummary = "yes"\n', "true or false"),
        ('[reports]\naggregate = 1\n', "reports.aggregate must be true or false"),
        ("[storage]\ndirectory = 3\n", "must be a path"),
        ('collections = ["dcr"]\n', "'collections' must be a mapping"),
        ('[collections]\ndcr = ["foo"]\n', "'collections.dcr' must be a mapping"),
        ('[collections.DCR]\npackages = ["foo"]\n', "collection names must be lowercase"),
        ('[collections."../up"]\npackages = ["foo"]\n', "collection names must be lowercase"),
        ('[collections.other]\npackages = ["foo"]\n', "reserved for packages in no collection"),
        ("[collections.dcr]\npackages = []\n", "at least one package"),
        ("[collections.dcr]\n", "collections.dcr.packages is required"),
        ('[collections.dcr]\npackages = ["../x"]\n', "invalid package name"),
        ('[collections.dcr]\npackages = ["foo"]\naggregate = "yes"\n', "collections.dcr.aggregate must be true or false"),
        ('[collections.dcr]\npackages = ["foo"]\ntitle = 3\n', "collections.dcr.title must be text"),
        ('[collections.dcr]\npackages = ["foo"]\ntotal = true\n', "unknown key"),
        ('[reports]\naggregate = true\n[collections.dcr]\npackages = ["foo"]\n', "set aggregate on each collection"),
        ('storage = "data/"\n', "must be a mapping"),
        ("packages = [\n", "invalid TOML"),
    ],
)
def test_invalid_configs_give_useful_errors(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, text))


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "nope.toml")


def test_normalize_packages_deduplicates_equivalent_names():
    assert normalize_packages(["Foo.Bar", "foo_bar", "foo-bar", "baz"]) == ["foo-bar", "baz"]


@pytest.mark.parametrize("name", ["", "..", "a/b", "-foo", "foo bar", "a/../b"])
def test_unsafe_package_names_are_rejected(name):
    with pytest.raises(ConfigError):
        normalize_packages([name])


def test_resolve_path_handles_relative_absolute_and_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    base = tmp_path / "base"
    assert resolve_path("x", base) == (base / "x").resolve()
    assert resolve_path(tmp_path / "abs", base) == (tmp_path / "abs").resolve()
    assert resolve_path("~/x", base) == (tmp_path / "x").resolve()
    monkeypatch.chdir(tmp_path)
    assert resolve_path("y") == (Path.cwd() / "y").resolve()


DIRECTORIES = '[storage]\ndirectory = "d"\n[output]\ndirectory = "o"\n'


class TestPathPrecedence:
    def test_built_in_defaults_are_relative_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tracker = Tracker()
        assert tracker.data_dir == Path.cwd() / "data"
        assert tracker.output_dir == Path.cwd() / "public"

    def test_config_values_beat_defaults_and_ignore_cwd(self, tmp_path, monkeypatch):
        path = write(tmp_path, DIRECTORIES)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        tracker = Tracker.from_config(path)
        assert tracker.data_dir == (path.parent / "d").resolve()
        assert tracker.output_dir == (path.parent / "o").resolve()

    def test_explicit_paths_beat_config_and_use_cwd(self, tmp_path, monkeypatch):
        path = write(tmp_path, DIRECTORIES)
        monkeypatch.chdir(tmp_path)
        tracker = Tracker.from_config(path, data_dir="mine", output_dir=tmp_path / "abs")
        assert tracker.data_dir == Path(os.getcwd()) / "mine"
        assert tracker.output_dir == tmp_path.resolve() / "abs"

    def test_config_without_directories_falls_back_to_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tracker = Tracker.from_config(write(tmp_path, 'packages = ["foo"]\n'))
        assert tracker.data_dir == Path.cwd() / "data"
