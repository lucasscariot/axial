"""The date filter offers calendar periods: weeks from Monday, whole months, periods to date."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from axial.app import create_app
from axial.checks import run_checks
from axial.config import load_config
from axial.registry import DATE_PRESETS, DateRange, Registry

D = dt.date


def span(preset, today):
    return DATE_PRESETS[preset].span(today)


@pytest.mark.parametrize("preset, today, expected", [
    ("today", D(2026, 11, 17), (D(2026, 11, 17), D(2026, 11, 18))),
    # 17 Nov 2026 is a Tuesday: the week started on Monday the 16th and includes today
    ("this_week", D(2026, 11, 17), (D(2026, 11, 16), D(2026, 11, 18))),
    ("this_week", D(2026, 11, 16), (D(2026, 11, 16), D(2026, 11, 17))),  # on a Monday: one day
    ("this_week", D(2026, 11, 22), (D(2026, 11, 16), D(2026, 11, 23))),  # on a Sunday: the full week
    ("last_week", D(2026, 11, 17), (D(2026, 11, 9), D(2026, 11, 16))),
    ("last_week", D(2026, 11, 22), (D(2026, 11, 9), D(2026, 11, 16))),
    ("last_week", D(2027, 1, 1), (D(2026, 12, 21), D(2026, 12, 28))),  # across a year end
    ("month_to_date", D(2026, 11, 17), (D(2026, 11, 1), D(2026, 11, 18))),
    ("month_to_date", D(2026, 11, 1), (D(2026, 11, 1), D(2026, 11, 2))),
    ("last_month", D(2026, 11, 17), (D(2026, 10, 1), D(2026, 11, 1))),
    ("last_month", D(2026, 1, 15), (D(2025, 12, 1), D(2026, 1, 1))),
    ("last_month", D(2024, 3, 31), (D(2024, 2, 1), D(2024, 3, 1))),  # a leap February
    ("quarter_to_date", D(2026, 11, 17), (D(2026, 10, 1), D(2026, 11, 18))),
    ("quarter_to_date", D(2026, 3, 31), (D(2026, 1, 1), D(2026, 4, 1))),
    ("year_to_date", D(2026, 11, 17), (D(2026, 1, 1), D(2026, 11, 18))),
    ("year_to_date", D(2024, 12, 31), (D(2024, 1, 1), D(2025, 1, 1))),
])
def test_periods(preset, today, expected):
    assert span(preset, today) == expected


def test_no_period_ever_exceeds_the_length_it_declares():
    day = D(2023, 1, 1)
    while day < D(2029, 1, 1):
        for p in DATE_PRESETS.values():
            start, end = p.span(day)
            assert 1 <= (end - start).days <= p.longest_days, (p.id, day)
        day += dt.timedelta(days=1)


def test_a_dashboard_offers_only_periods_that_always_fit():
    ids = lambda max_days: [p["id"] for p in DateRange(max_days=max_days).presets(D(2026, 11, 17))]  # noqa: E731
    assert ids(1) == ["today"]
    assert ids(7) == ["today", "this_week", "last_week"]
    assert ids(31) == ["today", "this_week", "last_week", "month_to_date", "last_month"]
    assert ids(92) == ids(31) + ["quarter_to_date"]
    assert ids(366) == ids(92) + ["year_to_date"]
    assert DateRange(max_days=31).presets(D(2026, 11, 17))[3] == {
        "id": "month_to_date", "label": "Month to date", "start": "2026-11-01", "end": "2026-11-18"}


def test_default_period_replaces_default_days():
    assert DateRange(default_days=7).default(D(2026, 11, 17)) == (D(2026, 11, 11), D(2026, 11, 18))
    assert DateRange(default_preset="last_month").default(D(2026, 11, 17)) == (D(2026, 10, 1), D(2026, 11, 1))


def dashboard(repo, date_range):
    (repo / "dashboards" / "booking_ops.yaml").write_text(
        f"title: Ops\ndate_range: {date_range}\ntiles:\n"
        "  - {type: line, query: bookings_daily, x: day, y: bookings}\n")
    return load_config(repo, environ={})


def test_api_sends_the_default_and_the_periods(repo):
    cfg = dashboard(repo, "{default: month_to_date, max_days: 31}")
    assert [i for i in run_checks(Registry(cfg)) if i.level == "error"] == []
    with TestClient(create_app(cfg, mode="fixture")) as c:
        dr = c.get("/api/dashboards/booking_ops").json()["date_range"]
    today = dt.date.today()
    assert (dr["default_start"], dr["default_end"]) == (today.replace(day=1).isoformat(),
                                                        (today + dt.timedelta(days=1)).isoformat())
    assert [p["label"] for p in dr["presets"]] == ["Today", "This week", "Last week", "Month to date", "Last month"]
    mtd = next(p for p in dr["presets"] if p["id"] == "month_to_date")
    assert (mtd["start"], mtd["end"]) == (dr["default_start"], dr["default_end"])


@pytest.mark.parametrize("date_range, message", [
    ("{default: last_fortnight, max_days: 31}", "date_range.default 'last_fortnight' is not a period"),
    ("{default: last_month, max_days: 7}", "date_range.default last_month can cover 31 days, more than max_days (7)"),
    ("{default: this_week, default_days: 7, max_days: 31}", "use default or default_days, not both"),
])
def test_check_rejects_a_bad_default_period(repo, date_range, message):
    issues = [i.message for i in run_checks(Registry(dashboard(repo, date_range))) if i.level == "error"]
    assert any(message in i for i in issues), issues
