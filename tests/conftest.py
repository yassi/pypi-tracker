"""Shared fixtures: a fake PyPIStats so no test can reach the network."""

import io
import json
from datetime import date, timedelta

import pytest


def overall_body(observations: dict[str, int]) -> bytes:
    rows = [
        {"category": "without_mirrors", "date": day, "downloads": downloads}
        for day, downloads in observations.items()
    ]
    return json.dumps({"data": rows}).encode()


def days(start: str, count: int, downloads: int = 10) -> dict[date, int]:
    """A history of `count` consecutive days."""
    first = date.fromisoformat(start)
    return {first + timedelta(days=offset): downloads for offset in range(count)}


class FakeApi:
    """Maps a package to its observations, or to an exception to raise."""

    def __init__(self):
        self.responses = {}
        self.requests = []

    def __call__(self, request, timeout):
        package = request.full_url.split("/packages/")[1].split("/")[0]
        self.requests.append(package)
        response = self.responses[package]
        if isinstance(response, Exception):
            raise response
        return io.BytesIO(overall_body(response))


@pytest.fixture(autouse=True)
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr("pypi_tracker.client.urlopen", fake)
    return fake


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr("pypi_tracker.client.time.sleep", recorded.append)
    return recorded
