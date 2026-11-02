"""Read daily download counts from the PyPIStats `/overall` endpoint."""

import http.client
import json
import time
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import PyPIStatsError

URL = "https://pypistats.org/api/packages/{package}/overall?mirrors=false"
CATEGORY = "without_mirrors"
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
MAX_WAIT_SECONDS = 120


def fetch_overall(package: str) -> dict[date, int]:
    """Return all daily observations PyPIStats has for a package."""
    request = Request(URL.format(package=package), headers={"User-Agent": "pypi-tracker"})
    problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = 2**attempt
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
        except HTTPError as error:
            if error.code == 404:
                raise PyPIStatsError("package not found on PyPIStats") from error
            if error.code != 429 and error.code < 500:
                raise PyPIStatsError(f"PyPIStats returned HTTP {error.code}") from error
            problem = f"PyPIStats returned HTTP {error.code}"
            wait = _retry_after(error.headers.get("Retry-After")) or wait
        except (URLError, OSError, http.client.HTTPException) as error:
            problem = f"network error: {error}"
        else:
            return parse_overall(body)

        if wait > MAX_WAIT_SECONDS:
            raise PyPIStatsError(f"{problem}; server asked to wait {wait:.0f} seconds")
        if attempt < MAX_ATTEMPTS:
            time.sleep(wait)
    raise PyPIStatsError(f"{problem} (after {MAX_ATTEMPTS} attempts)")


def parse_overall(body: bytes) -> dict[date, int]:
    """Turn a response body into {date: downloads}, rejecting anything suspicious."""
    observations: dict[date, int] = {}
    try:
        for row in json.loads(body)["data"]:
            if row["category"] != CATEGORY:
                continue
            day = date.fromisoformat(row["date"])
            downloads = row["downloads"]
            if type(downloads) is not int or downloads < 0:
                raise ValueError(f"invalid download count {downloads!r} for {day}")
            if day in observations:
                raise ValueError(f"duplicate observation for {day}")
            observations[day] = downloads
    except (ValueError, KeyError, TypeError) as error:
        raise PyPIStatsError(f"malformed PyPIStats response: {error!r}") from error
    return observations


def _retry_after(header: str | None) -> float | None:
    """Parse a Retry-After header, which is either seconds or an HTTP date."""
    if not header:
        return None
    try:
        return max(float(header), 0)
    except ValueError:
        pass
    try:
        return max((parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds(), 0)
    except (ValueError, TypeError):
        return None
