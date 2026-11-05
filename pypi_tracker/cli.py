"""The `pypi-tracker` command line. All real work happens in Tracker."""

import argparse
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from .config import resolve_path
from .errors import ConfigError, TrackerError
from .server import serve
from .tracker import Tracker

DEFAULT_CONFIG = "pypi-tracker.toml"
EXAMPLE_CONFIG = """\
packages = ["example-package"]

[reports]
rolling_days = [7, 30, 90, 180]
calendar = ["weekly", "monthly", "yearly"]
historical = ["daily"]
summary = true
aggregate = true

[dashboard]
enabled = true
title = "PyPI Package Statistics"

[storage]
directory = "data/"

[output]
directory = "public/"
"""


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args)
    except (TrackerError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pypi-tracker", description="Preserve PyPI download history and export static JSON."
    )
    # shortcut: needs installed package metadata, so the CLI won't start from a bare PYTHONPATH checkout.
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('pypi-tracker')}")
    commands = parser.add_subparsers(required=True)

    source = argparse.ArgumentParser(add_help=False)
    source.add_argument("--config", help=f"TOML configuration file (default: ./{DEFAULT_CONFIG} if it exists)")
    source.add_argument("--data-dir", help="directory of historical CSV files")
    source.add_argument(
        "--package", action="append", help="package to process, instead of the config's (repeatable)"
    )
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--output-dir", help="directory receiving JSON reports and the site")

    def add(name: str, handler, help: str, parents: list) -> argparse.ArgumentParser:
        command = commands.add_parser(name, help=help, parents=parents)
        command.set_defaults(command=handler)
        return command

    add("collect", _collect, "fetch observations and merge them into CSV storage", [source])
    add("build", _build, "generate JSON reports from stored history", [source, output])
    add("sync", _sync, "collect, then build", [source, output])
    add("dashboard", _dashboard, "build reports and a static website charting them", [source, output])
    serve_command = add(
        "serve", _serve, "generate the site and preview it locally, updating on changes", [source, output]
    )
    serve_command.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    add("validate", _validate, "check the historical dataset", [source])

    init = add("init", _init, "write an example configuration file", [])
    init.add_argument("--output", default=DEFAULT_CONFIG, help="where to write it")
    return parser


def _tracker(args: argparse.Namespace) -> Tracker:
    output_dir = getattr(args, "output_dir", None)
    config = args.config or (DEFAULT_CONFIG if Path(DEFAULT_CONFIG).exists() else None)
    if config:
        return Tracker.from_config(
            config, data_dir=args.data_dir, output_dir=output_dir, packages=args.package
        )
    paths = {"data_dir": args.data_dir, "output_dir": output_dir}
    return Tracker(args.package or (), **{key: value for key, value in paths.items() if value})


def _collect(args: argparse.Namespace) -> int:
    tracker = _tracker(args)
    changed = tracker.collect()
    print(f"collected {len(tracker.packages)} package(s), {len(changed)} changed")
    return 0


def _build(args: argparse.Namespace) -> int:
    written = _tracker(args).build()
    print(f"built reports, {len(written)} file(s) changed")
    return 0


def _sync(args: argparse.Namespace) -> int:
    changed, written = _tracker(args).sync()
    print(f"synced: {len(changed)} package(s) changed, {len(written)} report file(s) changed")
    return 0


def _dashboard(args: argparse.Namespace) -> int:
    tracker = _tracker(args)
    tracker.dashboard()
    print(f"wrote {tracker.output_dir / 'index.html'}")
    print("preview it with: pypi-tracker serve")
    return 0


def _serve(args: argparse.Namespace) -> int:
    serve(lambda: _tracker(args), resolve_path(args.config or DEFAULT_CONFIG), args.port)
    return 0


def _validate(args: argparse.Namespace) -> int:
    results = _tracker(args).validate()
    for package, result in results.items():
        print(f"{package}: {result['recorded_days']} recorded day(s)")
        for problem in result["problems"]:
            print(f"  problem: {problem}")
        for gap in result["gaps"]:
            print(f"  coverage gap: {gap['start']} to {gap['end']} ({gap['days']} day(s))")
    return 1 if any(result["problems"] for result in results.values()) else 0


def _init(args: argparse.Namespace) -> int:
    path = resolve_path(args.output)
    if path.exists():
        raise ConfigError(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"wrote {path}")
    return 0
