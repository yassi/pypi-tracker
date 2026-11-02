# pypi-tracker

pypi-tracker is a lightweight tool for collecting and preserving PyPI package download statistics. It maintains historical data beyond [PyPIStats](https://pypistats.org)' retention window. It generates static, chart-ready JSON reports that can be used to visualize download trends, track package adoption, and power dashboards or other applications without a backend.


## Try it

```bash
pip install pypi-tracker


pypi-tracker sync --package dj-control-room
```

You now have the full history in `data/dj-control-room.csv` and reports in
`public/packages/dj-control-room/`, such as `last-30-days.json`:

```json
{
  "package": "dj-control-room",
  "report": "last-30-days",
  "as_of": "2026-11-01",
  "downloads": 38071,
  "complete": true,
  "observations": [
    {"date": "2026-10-03", "downloads": 510},
    {"date": "2026-10-04", "downloads": 757}
  ]
}
```

The `observations` list has one entry per day in the window (shortened here). A day with no data is `null`,
never `0`, and `complete` tells you whether any day is missing.

## Commands

| Command | What it does |
|---|---|
| `init` | Creates a starter config file. |
| `sync` | Runs `collect`, then `build`. This is the one to schedule. |
| `collect` | Fetches new data from PyPIStats and merges it into the CSVs. |
| `build` | Writes the JSON report files to publish. |
| `validate` | Checks the CSVs for bad data and missing dates. |

## Tracking several packages

You will typically want to keep a config file where you specify the packages you want to track.

```bash
pypi-tracker init  # generates a new pypi-tracker.toml file in the current directory
```

A complete config file will have the sections seen below. Note that the `pypi-tracker` also
allows for cli args that can supersede these config values; though typically it is much
easier to simply rely on a config file.
```toml
# pypi-tracker.toml
packages = [
    "dj-control-room",
    "dj-control-room-base",
    "dj-redis-panel",
    "dj-cache-panel",
    "dj-celery-panel",
    "dj-urls-panel",
    "dj-signals-panel",
]

[reports]
rolling_days = [7, 30, 90, 180]
calendar = ["weekly", "monthly", "yearly"]
historical = ["daily"]
summary = true

[storage]
directory = "data/"

[output]
directory = "public/"
```

You can update both reports and historical data by running a single `sync` command.
```bash
pypi-tracker sync --config pypi-tracker.toml
```

## Keep your CSVs

Run `sync` on a schedule and commit `data/`, because **the CSV files are your history**. If you lose them, or
stop collecting for longer than PyPIStats keeps data, those days are gone. History also can't reach back
further than PyPIStats did on your first run, so totals are "recorded" downloads, not lifetime downloads.

## More

The [guide](docs/guide.md) covers configuration, every report format, the Python API, and GitHub Actions and
Cloudflare Pages setup.
