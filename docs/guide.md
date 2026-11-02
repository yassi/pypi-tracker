# pypi-tracker guide

Preserve PyPI download history permanently and publish it as static JSON.

[PyPIStats](https://pypistats.org) only keeps about 180 days of daily downloads. `pypi-tracker` reads
that data regularly, merges it into CSV files that you keep, and turns those files into JSON reports
that any static host can serve. There is no database and no server.

It builds on PyPIStats rather than replacing it: PyPIStats is still the only data source.

## Installation

Requires Python 3.12+. There are no third-party dependencies.

```bash
pip install pypi-tracker
```

From a checkout: `pip install .` (add `".[dev]"` and run `pytest` to run the tests).

## Quick start

```bash
pypi-tracker init --output pypi-tracker.toml   # then edit the package list
pypi-tracker sync --config pypi-tracker.toml
```

This writes `data/<package>.csv` and `public/packages/<package>/*.json` next to the config file.

## Configuration

A config file is optional if you pass packages directly, and can live anywhere.

```toml
packages = ["dj-control-room", "dj-cache-panel"]  # must come before the first [section]

[reports]                                         # every key is optional; these are the defaults
rolling_days = [7, 30, 90, 180]                   # any positive integers
calendar = ["weekly", "monthly", "yearly"]
historical = ["daily"]
summary = true

[storage]
directory = "data/"

[output]
directory = "public/"
```

Use an empty list, or `summary = false`, to turn a report off. Unknown keys and invalid values are errors.

| Change to `packages` | What happens |
|---|---|
| Add a package | The next collection backfills everything PyPIStats still has. |
| Remove a package | Collection and building stop. Its CSV and existing reports stay in place. |
| Re-add a package | Collection merges into the existing CSV. Dates missed while it was removed remain visible as gaps unless PyPIStats still has them. |

Package names are [normalized](https://peps.python.org/pep-0503/#normalized-names), so `Foo_Bar` and
`foo-bar` share one CSV file.

## Input and output paths

| Option | Purpose | Config key | Default |
|---|---|---|---|
| `--config` | TOML configuration file | | none (not auto-discovered) |
| `--data-dir` | Historical CSV files | `storage.directory` | `data` |
| `--output-dir` | Generated JSON reports | `output.directory` | `public` |

An explicit CLI argument (or Python argument) wins over the config value, which wins over the default.

- CLI and Python paths, and the defaults, are relative to the current working directory.
- Paths inside the config file are relative to the directory containing the config file, so the same config works
  from anywhere.
- Absolute paths and `~` work everywhere.

Output directories are created as needed. A CSV that an operation requires is never silently created.

## Commands

All commands except `init` accept `--config`, `--data-dir` and repeatable `--package`. Without `--config`,
`./pypi-tracker.toml` is used if it exists. `--package`
replaces the config's package list. `build`, `sync` and `dashboard` also accept `--output-dir`.

Every command exits nonzero on failure.

| Command | What it does | Network? |
|---|---|---|
| `collect` | Fetch observations and merge them into the CSVs | Yes |
| `build` | Generate JSON reports from the CSVs | No |
| `sync` | `collect`, then `build`; the command to automate | Yes |
| `dashboard` | `build`, then write a static website charting the reports | No |
| `validate` | Check the CSVs | No |
| `init` | Write an example config (never overwrites a file) | No |

```bash
pypi-tracker collect --package dj-control-room --data-dir data/
pypi-tracker sync --config config/packages.toml --data-dir data/ --output-dir public/

pypi-tracker validate --config config/packages.toml
```

Without a package list, `build`, `dashboard` and `validate` use every CSV in the data directory. `collect`
and `sync` need an explicit list.

`validate` fails on malformed dates or counts, negative counts, duplicates and out-of-order rows.
Missing dates are reported as coverage gaps but do not fail it, since a gap is not invalid data.

If `collect` fails for a package, the remaining packages still run and the failures are listed. If
`sync` fails for a package, the healthy packages are still published, the failed package's reports are
left alone, and the command exits nonzero.

## Python API

```python
from pypi_tracker import Tracker

tracker = Tracker.from_config(
    "config/packages.toml", data_dir="data/", output_dir="public/"
)
# or: tracker = Tracker(packages=["dj-control-room"], data_dir="data/", output_dir="public/")

tracker.collect()  # returns the packages whose CSV changed
tracker.build()  # returns the report files that changed
tracker.sync()  # returns both
tracker.dashboard()  # builds reports and the website, and returns the files that changed
tracker.validate()  # {package: {"problems": [...], "gaps": [...], "recorded_days": n}}

history = tracker.history("dj-control-room")  # {datetime.date: int}
```

`collect`, `build` and `sync` try every package and then raise `PartialFailure` if any failed; its
`failures` attribute maps package names to reasons. All expected errors inherit from `TrackerError`.

The calculations are plain functions over that history: `rolling_window(history, 30)`,
`weekly(history)`, `monthly(history)`, `yearly(history)`, `summarize(history)` and `find_gaps(history)`.
Pass `reports=ReportSettings(rolling_days=[14], ...)` to `Tracker` to choose which reports `build` writes.

## Historical CSV storage

CSV files are the source of truth, with one file per package:

```csv
date,downloads
2026-05-01,1234
2026-05-02,0
```

Each collection reads the CSV, fetches all available days, and merges by date:

- New dates are added.
- If PyPIStats has revised a date, its new value replaces the stored one.
- Dates PyPIStats no longer returns are kept forever.

The file is replaced atomically, and only if its content changed, so running collection twice is
harmless. If a request fails, the existing file is untouched. If a CSV is invalid (for example it
contains merge conflict markers), collection refuses to touch it and reports why.

There is one request per package per run. Comparing a full window on each run is what makes corrections
possible, so collection does not skip packages that look up to date.

## JSON reports

```text
public/packages/dj-control-room/
├── summary.json
├── last-7-days.json       (one file per configured window)
├── last-30-days.json
├── weekly.json
├── monthly.json
├── yearly.json
└── daily/
    ├── 2025.json
    └── 2026.json
```

Reports are generated only from local data. Output is deterministic, each file is replaced atomically
and only if changed, and other files in the output directory are left alone. Files contain no generation
timestamp, because it would change every file on every run. Use `as_of` to see how fresh the data is.

**Rolling windows** cover the N days ending at the latest recorded date, not today's date. They are
self-contained, so a chart or table needs only this one file: `observations` always has exactly N entries
in date order, one per day. A day with no data has `"downloads": null`.

```json
{
  "package": "dj-control-room",
  "report": "last-30-days",
  "as_of": "2026-10-30",
  "period": {"start": "2026-10-01", "end": "2026-10-30", "days": 30},
  "downloads": 37394,
  "recorded_days": 30,
  "missing_days": 0,
  "complete": true,
  "observations": [
    {"date": "2026-10-01", "downloads": 1204},
    {"date": "2026-10-02", "downloads": 1187},
    "... 28 more, ending at 2026-10-30"
  ]
}
```

**Calendar reports** (`weekly.json`, `monthly.json`, `yearly.json`) contain a `periods` list covering all
recorded history. Weeks are ISO weeks, Monday through Sunday. Consumers can derive growth or comparisons
from these.

```json
{
  "package": "dj-control-room",
  "report": "monthly",
  "as_of": "2026-10-30",
  "periods": [
    {
      "month": "2026-10",
      "start": "2026-10-01",
      "end": "2026-10-31",
      "days": 31,
      "downloads": 37394,
      "recorded_days": 30,
      "missing_days": 1,
      "complete": false
    }
  ]
}
```

The identifier key is `week` (`"2026-W44"`), `month` (`"2026-10"`) or `year` (`2026`).

**Daily reports** are split by year, so no file grows forever: `{"package": ..., "year": 2026,
"observations": [{"date": "2026-10-30", "downloads": 1300}]}`.

**`summary.json`** holds `as_of`, `earliest_date`, `latest_date`, `recorded_downloads`, `recorded_days`,
`missing_days` and `gaps` (a list of `{start, end, days}`).

## Aggregate reports

`build` also writes the same reports for the whole collection to `aggregate/`, with `packages` (a list)
in place of `package`. Two rules make them honest:

- **A date counts only when every package has data for it.** Other dates are `null`, never a partial sum,
  and rolling windows end on the latest date all packages share.
- **Calendar reports keep every period since the first observation.** Periods from before a newer package
  joined stay in the file, marked incomplete with their real `recorded_days`.

Aggregate downloads are a sum of downloads, not unique installs or users.

Aggregate rolling reports add `breakdown`, each package's own numbers for that window:
`{package, downloads, recorded_days, missing_days, previous_downloads}`. Aggregate `summary.json` adds
`first_observed_date`, the earliest date from any package. Rows count each package's own recorded days, so
with missing data they can add up to more than the aggregate total.

## Dashboard

`pypi-tracker dashboard` runs `build`, then writes a complete static site to the output directory:
`index.html`, plus `assets/app.js`, `assets/style.css` and your logo. It has no dependencies, loads nothing
from a CDN, and never calls PyPIStats. Publish the whole output directory to any static host, at any path.

```bash
pypi-tracker dashboard --config pypi-tracker.toml
python -m http.server -d public  # then open http://localhost:8000/
```

Everything in the `[dashboard]` section is optional:

```toml
[dashboard]
enabled = true                # build and sync write the site too, so you don't need a separate step
title = "Django Control Room"
description = "Download statistics across the DCR ecosystem"
logo = "assets/logo.svg"      # .svg, .png or .webp, relative to this config file; also the favicon
theme = "auto"                # auto, light or dark; visitors can still switch
accent_color = "#6366f1"
```

- The site needs `reports.historical = ["daily"]` and `reports.summary = true`, and fails with a clear
  error without them.
- It won't overwrite an `index.html` that pypi-tracker didn't generate.
- It doesn't work when opened directly from disk, because browsers block those requests.
- Selections are kept in the URL, such as `?range=90d&packages=foo,bar&granularity=weekly`, so views can
  be shared. `view=packages` shows one line per package, up to the eight largest.
- The first view loads only `aggregate/summary.json` and one rolling report. Other views fetch each
  selected package's yearly files and are combined in the browser.
- Average and change figures use recorded days only. Change is shown only when both periods are complete.

## Missing data and completeness

Every report follows the same rules:

- A missing date is not a zero. A recorded `0` is an observation; a missing date is absent from the CSV
  and from `daily/` files, and is `null` in a rolling report's `observations`.
- `complete` is `true` only if every day in the period is recorded. An in-progress month is incomplete,
  as is the partial first month of a package's history.
- For an incomplete period, `downloads` is the sum of the recorded days, and `recorded_days` and
  `missing_days` show the shortfall. Treat that total as a lower bound.
- A period with no recorded days has `"downloads": null`. It still appears in calendar reports if it falls
  within the recorded history. With no data at all, rolling reports have `null` dates and `as_of`, and empty
  `observations`.

## PyPIStats limitations

- Only `/overall?mirrors=false` is used, counting the `without_mirrors` category.
- **History starts when PyPIStats' window started on your first collection.** Older data cannot be
  reconstructed, so totals are "recorded downloads", not lifetime downloads.
- **If you stop collecting for longer than PyPIStats' retention window, the missed days are lost** and
  remain as gaps.
- The current UTC day is never stored because it is incomplete. PyPIStats does not say whether its newest
  earlier day is final; if that value is later revised, the next collection picks up the correction.
- PyPIStats appears to leave out days with no downloads, rather than reporting `0`. Gaps seen so far are
  all next to very low daily counts, so a low-traffic package may show "missing" days that were really
  zero. This is an observation, not documented behavior, so these days are still treated as missing.
- Requests retry up to 4 times on network errors, rate limiting and server errors, with backoff, honoring
  `Retry-After`. A `Retry-After` over 2 minutes fails that package rather than retrying early.

## GitHub Actions

CI runners are ephemeral, so **the CSV files must be persisted between runs, or long-term history is
lost**. Committing them to Git is the simplest way:

```yaml
name: Update download stats
on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install git+https://github.com/yassi/pypi-tracker.git
      - id: sync
        run: pypi-tracker sync --config config/packages.toml --data-dir data/ --output-dir public/
      # Runs even if sync failed for one package, so the others' collected data is not lost.
      - if: always()
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/
          git diff --cached --quiet || git commit -m "Update download history"
          git push
```

A nonzero exit from `sync` fails the workflow, so a bad refresh is visible. Pin a tag or commit in the
install line once you depend on this in production.

## Cloudflare Pages

Publish `public/` with [Wrangler](https://developers.cloudflare.com/pages/get-started/direct-upload/) as
a final workflow step, or use `actions/upload-pages-artifact` for GitHub Pages:

```yaml
      - if: steps.sync.outcome == 'success'
        uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          command: pages deploy public --project-name=my-stats
```

To publish the [dashboard](#dashboard) too, set `enabled = true` in its config section. It's served from the same origin as the reports, so it doesn't need CORS.

A browser on another origin can only read the JSON with CORS enabled. On Cloudflare Pages, add a
`public/_headers` file; `build` leaves it in place:

```text
/packages/*
  Access-Control-Allow-Origin: *
/aggregate/*
  Access-Control-Allow-Origin: *
```

## Not in scope

No database, hosted backend, REST server, authentication, package discovery, plugins, GitHub
statistics or growth-rate reports.
