import io
import json
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from urllib.error import HTTPError, URLError

import pytest
from conftest import overall_body

from pypi_tracker.client import MAX_ATTEMPTS, fetch_overall, parse_overall
from pypi_tracker.errors import PyPIStatsError


def http_error(code, headers=None):
    return HTTPError("https://example.test", code, "error", headers or {}, None)


def test_uses_only_overall_without_mirrors(monkeypatch):
    urls = []

    def fake(request, timeout):
        urls.append(request.full_url)
        raise http_error(404)

    monkeypatch.setattr("pypi_tracker.client.urlopen", fake)
    with pytest.raises(PyPIStatsError):
        fetch_overall("foo")
    assert urls == ["https://pypistats.org/api/packages/foo/overall?mirrors=false"]


def test_never_counts_other_categories():
    body = json.dumps(
        {
            "data": [
                {"category": "without_mirrors", "date": "2026-01-01", "downloads": 5},
                {"category": "with_mirrors", "date": "2026-01-01", "downloads": 900},
            ]
        }
    )
    assert parse_overall(body.encode()) == {date(2026, 1, 1): 5}


@pytest.mark.parametrize(
    "body",
    [
        b"<html>",
        b"[]",
        b"{}",
        b'{"data": 3}',
        b'{"data": [{"category": "without_mirrors"}]}',
        b'{"data": [{"category": "without_mirrors", "date": "nope", "downloads": 1}]}',
        b'{"data": [{"category": "without_mirrors", "date": "2026-01-01", "downloads": -1}]}',
        b'{"data": [{"category": "without_mirrors", "date": "2026-01-01", "downloads": 1.5}]}',
        b'{"data": [{"category": "without_mirrors", "date": "2026-01-01", "downloads": true}]}',
    ],
)
def test_malformed_responses_are_rejected(body):
    with pytest.raises(PyPIStatsError, match="malformed"):
        parse_overall(body)


def test_duplicate_dates_are_rejected():
    row = {"category": "without_mirrors", "date": "2026-01-01", "downloads": 1}
    with pytest.raises(PyPIStatsError, match="duplicate"):
        parse_overall(json.dumps({"data": [row, row]}).encode())


def test_missing_package_is_not_retried(api, sleeps):
    api.responses["ghost"] = http_error(404)
    with pytest.raises(PyPIStatsError, match="not found"):
        fetch_overall("ghost")
    assert api.requests == ["ghost"]
    assert sleeps == []


def test_network_errors_retry_a_bounded_number_of_times(api, sleeps):
    api.responses["foo"] = URLError("down")
    with pytest.raises(PyPIStatsError, match="network error"):
        fetch_overall("foo")
    assert len(api.requests) == MAX_ATTEMPTS
    assert sleeps == [2, 4, 8]


def test_timeouts_and_server_errors_are_retried(api, sleeps):
    api.responses["foo"] = TimeoutError("slow")
    with pytest.raises(PyPIStatsError):
        fetch_overall("foo")
    api.responses["bar"] = http_error(503)
    with pytest.raises(PyPIStatsError, match="HTTP 503"):
        fetch_overall("bar")
    assert api.requests.count("bar") == MAX_ATTEMPTS


def test_rate_limit_respects_retry_after_seconds(api, sleeps):
    api.responses["foo"] = http_error(429, {"Retry-After": "7"})
    with pytest.raises(PyPIStatsError, match="HTTP 429"):
        fetch_overall("foo")
    assert sleeps == [7, 7, 7]


def test_rate_limit_respects_retry_after_date(api, sleeps):
    soon = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    api.responses["foo"] = http_error(429, {"Retry-After": soon})
    with pytest.raises(PyPIStatsError):
        fetch_overall("foo")
    assert all(50 < wait <= 60 for wait in sleeps)


def test_retry_after_in_the_past_falls_back_to_backoff(api, sleeps):
    api.responses["foo"] = http_error(429, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    with pytest.raises(PyPIStatsError):
        fetch_overall("foo")
    assert sleeps == [2, 4, 8]


def test_excessive_retry_after_fails_instead_of_retrying_early(api, sleeps):
    api.responses["foo"] = http_error(429, {"Retry-After": "86400"})
    with pytest.raises(PyPIStatsError, match="asked to wait"):
        fetch_overall("foo")
    assert api.requests == ["foo"]
    assert sleeps == []


def test_recovers_after_a_transient_failure(monkeypatch, sleeps):
    attempts = []

    def flaky(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise http_error(500)
        return io.BytesIO(overall_body({"2026-01-01": 3}))

    monkeypatch.setattr("pypi_tracker.client.urlopen", flaky)
    assert fetch_overall("foo") == {date(2026, 1, 1): 3}
