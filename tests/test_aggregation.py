from datetime import date

import pytest
from conftest import days

from pypi_tracker import find_gaps, monthly, rolling_window, summarize, weekly, yearly


class TestRollingWindow:
    def test_complete_window_anchors_to_latest_observation_not_today(self):
        window = rolling_window(days("2020-03-01", 30), 7)
        assert window == {
            "start": "2020-03-24",
            "end": "2020-03-30",
            "days": 7,
            "downloads": 70,
            "recorded_days": 7,
            "missing_days": 0,
            "complete": True,
            "observations": [
                {"date": f"2020-03-{day}", "downloads": 10} for day in range(24, 31)
            ],
        }

    def test_observations_have_one_chronological_entry_per_day(self):
        window = rolling_window(days("2026-01-01", 40), 30)
        dates = [point["date"] for point in window["observations"]]
        assert len(dates) == 30
        assert (dates[0], dates[-1]) == (window["start"], window["end"])
        assert dates == sorted(dates)

    def test_observations_show_missing_days_as_null_not_zero(self):
        history = days("2026-01-01", 3, downloads=0)
        del history[date(2026, 1, 2)]
        assert rolling_window(history, 4)["observations"] == [
            {"date": "2025-12-31", "downloads": None},
            {"date": "2026-01-01", "downloads": 0},
            {"date": "2026-01-02", "downloads": None},
            {"date": "2026-01-03", "downloads": 0},
        ]

    def test_boundaries_are_inclusive_and_exact(self):
        history = days("2026-01-01", 10)
        assert rolling_window(history, 10)["complete"] is True
        too_long = rolling_window(history, 11)
        assert too_long["start"] == "2025-12-31"
        assert too_long["complete"] is False
        assert too_long["missing_days"] == 1
        assert too_long["downloads"] == 100

    def test_one_day_window(self):
        window = rolling_window(days("2026-01-01", 3), 1)
        assert (window["start"], window["end"], window["downloads"]) == (
            "2026-01-03",
            "2026-01-03",
            10,
        )

    def test_gap_makes_window_incomplete_without_skipping_past_it(self):
        history = days("2026-01-01", 10)
        del history[date(2026, 1, 8)]
        window = rolling_window(history, 7)
        assert window["start"] == "2026-01-04"
        assert window["complete"] is False
        assert window["missing_days"] == 1
        assert window["downloads"] == 60

    def test_zero_download_days_are_observations(self):
        window = rolling_window(days("2026-01-01", 7, downloads=0), 7)
        assert window["downloads"] == 0
        assert window["complete"] is True

    def test_empty_history_is_unavailable(self):
        window = rolling_window({}, 7)
        assert window["downloads"] is None
        assert window["complete"] is False
        assert window["start"] is None
        assert window["observations"] == []

    def test_arbitrary_window_and_invalid_window(self):
        assert rolling_window(days("2026-01-01", 45), 45)["downloads"] == 450
        with pytest.raises(ValueError):
            rolling_window({}, 0)


class TestWeekly:
    def test_iso_week_runs_monday_through_sunday(self):
        [week] = weekly(days("2026-10-26", 7))
        assert week["week"] == "2026-W44"
        assert (week["start"], week["end"]) == ("2026-10-26", "2026-11-01")
        assert week["complete"] is True

    def test_week_crossing_new_year_keeps_its_iso_year(self):
        # 2027-01-01 is a Friday in ISO week 53 of 2026.
        [week] = weekly(days("2026-12-28", 7))
        assert week["week"] == "2026-W53"
        assert (week["start"], week["end"]) == ("2026-12-28", "2027-01-03")

    def test_first_week_of_iso_year_starting_in_december(self):
        # 2025-12-29 is the Monday of 2026-W01.
        assert weekly(days("2025-12-29", 1))[0]["week"] == "2026-W01"

    def test_partial_weeks_are_marked(self):
        first, second = weekly(days("2026-10-31", 3))  # Sat, Sun, Mon
        assert (first["start"], first["recorded_days"], first["complete"]) == ("2026-10-26", 2, False)
        assert (second["start"], second["recorded_days"], second["complete"]) == ("2026-11-02", 1, False)


class TestMonthly:
    def test_month_boundaries(self):
        january, february = monthly(days("2026-01-01", 59))
        assert (january["month"], january["start"], january["end"]) == (
            "2026-01",
            "2026-01-01",
            "2026-01-31",
        )
        assert january["downloads"] == 310
        assert (february["end"], february["complete"]) == ("2026-02-28", True)

    def test_leap_february(self):
        [february] = monthly(days("2024-02-01", 29))
        assert (february["end"], february["days"], february["complete"]) == ("2024-02-29", 29, True)

    def test_partial_month_is_not_complete(self):
        [october] = monthly(days("2026-10-01", 30))
        assert october["complete"] is False
        assert (october["days"], october["recorded_days"], october["missing_days"]) == (31, 30, 1)
        assert october["downloads"] == 300

    def test_month_with_no_data_stays_visible_and_is_not_zero(self):
        history = {date(2026, 1, 15): 5, date(2026, 3, 15): 5}
        february = monthly(history)[1]
        assert february["month"] == "2026-02"
        assert february["downloads"] is None
        assert february["complete"] is False


class TestYearly:
    def test_complete_and_partial_years(self):
        leap, partial = yearly(days("2024-01-01", 367))
        assert (leap["year"], leap["days"], leap["complete"]) == (2024, 366, True)
        assert (leap["start"], leap["end"]) == ("2024-01-01", "2024-12-31")
        assert (partial["year"], partial["recorded_days"], partial["complete"]) == (2025, 1, False)

    def test_all_recorded_history_is_preserved(self):
        history = {date(2020, 6, 1): 1, date(2026, 6, 1): 2}
        assert [year["year"] for year in yearly(history)] == list(range(2020, 2027))


def test_calendar_reports_of_empty_history_are_empty():
    assert weekly({}) == monthly({}) == yearly({}) == []


def test_find_gaps():
    history = {date(2026, 1, 1): 1, date(2026, 1, 2): 1, date(2026, 1, 6): 1, date(2026, 1, 8): 1}
    assert find_gaps(history) == [
        {"start": "2026-01-03", "end": "2026-01-05", "days": 3},
        {"start": "2026-01-07", "end": "2026-01-07", "days": 1},
    ]
    assert find_gaps({}) == []


def test_summarize():
    history = {date(2026, 1, 1): 3, date(2026, 1, 3): 4}
    assert summarize(history) == {
        "earliest_date": "2026-01-01",
        "latest_date": "2026-01-03",
        "recorded_downloads": 7,
        "recorded_days": 2,
        "missing_days": 1,
        "gaps": [{"start": "2026-01-02", "end": "2026-01-02", "days": 1}],
    }
    assert summarize({})["earliest_date"] is None
    assert summarize({})["recorded_downloads"] == 0
