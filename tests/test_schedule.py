from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from friday.domain.schedule import ScheduleSpec, next_occurrence, parse_timezone


def spec(kind: str = "daily", at: str = "07:30", days: list[str] | None = None) -> ScheduleSpec:
    return ScheduleSpec.model_validate({"kind": kind, "time": at, "days": days or []})


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def local(value: datetime, zone: str) -> str:
    return value.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M %Z")


# spec validation


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "hourly", "time": "07:30"},
        {"kind": "daily", "time": "7:30"},
        {"kind": "daily", "time": "24:00"},
        {"kind": "daily", "time": "07:60"},
        {"kind": "daily", "time": "07:30", "days": ["mon"]},
        {"kind": "weekly", "time": "07:30"},
        {"kind": "weekly", "time": "07:30", "days": []},
        {"kind": "weekly", "time": "07:30", "days": ["mon", "mon"]},
        {"kind": "weekly", "time": "07:30", "days": ["funday"]},
        {"kind": "daily", "time": "07:30", "extra": 1},
    ],
)
def test_invalid_specs_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScheduleSpec.model_validate(payload)


def test_unknown_timezones_are_rejected() -> None:
    assert parse_timezone("Asia/Kolkata").key == "Asia/Kolkata"
    for bad in ("Mars/Base", "", "../etc/passwd", "not a zone"):
        with pytest.raises(ValueError):
            parse_timezone(bad)


# next occurrence


def test_daily_in_a_fixed_offset_zone() -> None:
    kolkata = parse_timezone("Asia/Kolkata")  # UTC+5:30, no DST
    nxt = next_occurrence(spec(at="07:30"), kolkata, utc(2026, 10, 29, 0, 0))
    assert nxt == utc(2026, 10, 29, 2, 0)  # 07:30 IST


def test_the_result_is_strictly_after_the_reference_instant() -> None:
    kolkata = parse_timezone("Asia/Kolkata")
    exact = utc(2026, 10, 29, 2, 0)  # exactly 07:30 IST
    assert next_occurrence(spec(at="07:30"), kolkata, exact) == exact + timedelta(days=1)
    assert next_occurrence(spec(at="07:30"), kolkata, exact - timedelta(seconds=1)) == exact


def test_the_reference_may_be_in_any_timezone() -> None:
    zone = parse_timezone("Pacific/Auckland")
    reference = datetime(2026, 10, 29, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert next_occurrence(spec(at="09:00"), zone, reference).tzinfo is UTC
    with pytest.raises(ValueError):
        next_occurrence(spec(), zone, datetime(2026, 10, 29, 12, 0))  # naive is refused


def test_weekly_picks_the_listed_days_only() -> None:
    zone = parse_timezone("UTC")
    weekly = spec("weekly", "09:00", ["mon", "wed", "fri"])
    seen = []
    current = utc(2026, 10, 28, 12, 0)  # a Wednesday after 09:00
    for _ in range(6):
        current = next_occurrence(weekly, zone, current)
        seen.append(current.strftime("%a %H:%M"))
    assert seen == ["Fri 09:00", "Mon 09:00", "Wed 09:00", "Fri 09:00", "Mon 09:00", "Wed 09:00"]


def test_weekday_is_evaluated_in_the_schedules_timezone_not_utc() -> None:
    zone = parse_timezone("Pacific/Auckland")  # Monday 08:00 NZ is still Sunday in UTC
    nxt = next_occurrence(spec("weekly", "08:00", ["mon"]), zone, utc(2026, 10, 25, 0, 0))
    assert local(nxt, "Pacific/Auckland").startswith("2026-10-26 08:00")
    assert nxt.strftime("%a") == "Sun"


@pytest.mark.parametrize(
    ("zone", "at", "after", "expected_local"),
    [
        # US spring forward: 02:30 does not exist on 2026-03-08 -> shifted forward by the gap.
        ("America/New_York", "02:30", utc(2026, 3, 8, 5, 0), "2026-03-08 03:30 EDT"),
        ("America/New_York", "02:30", utc(2026, 3, 8, 8, 0), "2026-03-09 02:30 EDT"),
        # UK spring forward: 01:30 does not exist on 2026-03-29.
        ("Europe/London", "01:30", utc(2026, 3, 29, 0, 0), "2026-03-29 02:30 BST"),
        # US fall back: 01:30 occurs twice on 2026-11-01; the first (EDT) is used, once.
        ("America/New_York", "01:30", utc(2026, 11, 1, 0, 0), "2026-11-01 01:30 EDT"),
        ("America/New_York", "01:30", utc(2026, 11, 1, 6, 0), "2026-11-02 01:30 EST"),
        # UK fall back on 2026-10-25.
        ("Europe/London", "01:30", utc(2026, 10, 25, 0, 0), "2026-10-25 01:30 BST"),
        # Southern hemisphere: NZ springs forward on 2026-09-27 (02:00 -> 03:00).
        ("Pacific/Auckland", "02:30", utc(2026, 9, 26, 10, 0), "2026-09-27 03:30 NZDT"),
    ],
)
def test_dst_transitions(zone: str, at: str, after: datetime, expected_local: str) -> None:
    nxt = next_occurrence(spec(at=at), parse_timezone(zone), after)
    assert local(nxt, zone) == expected_local


def test_midnight_leap_day_and_year_boundary() -> None:
    zone = parse_timezone("UTC")
    assert next_occurrence(spec(at="00:00"), zone, utc(2026, 12, 31, 12, 0)) == utc(
        2027, 1, 1, 0, 0
    )
    assert next_occurrence(spec(at="23:59"), zone, utc(2028, 2, 28, 23, 59)) == utc(
        2028, 2, 29, 23, 59
    )
    assert next_occurrence(spec(at="12:00"), zone, utc(2028, 2, 29, 12, 0)) == utc(
        2028, 3, 1, 12, 0
    )


@pytest.mark.parametrize(
    "zone",
    [
        "America/New_York",
        "Europe/London",
        "Australia/Sydney",
        "Pacific/Auckland",
        "Asia/Kolkata",
        "America/Sao_Paulo",
        "Africa/Cairo",
    ],
)
@pytest.mark.parametrize("at", ["00:30", "02:30", "03:00", "12:00", "23:30"])
def test_a_full_year_has_exactly_one_run_per_local_day_in_order(zone: str, at: str) -> None:
    tz = parse_timezone(zone)
    daily = spec(at=at)
    current = utc(2026, 1, 1, 0, 0)
    days = []
    for _ in range(366):
        nxt = next_occurrence(daily, tz, current)
        assert nxt > current  # strictly increasing: never repeats an instant
        days.append(nxt.astimezone(tz).date())
        current = nxt
    assert len(days) == len(set(days))  # no local day twice (fall-back runs once)
    gaps = {(b - a).days for a, b in zip(days, days[1:], strict=False)}
    assert gaps == {1}  # and no local day skipped (spring-forward still runs)


def test_weekly_dst_week_runs_on_each_listed_day_once() -> None:
    tz = parse_timezone("America/New_York")
    weekly = spec("weekly", "02:30", ["sun"])
    current = utc(2026, 3, 1, 0, 0)
    runs = []
    for _ in range(3):
        current = next_occurrence(weekly, tz, current)
        runs.append(local(current, "America/New_York"))
    assert runs == ["2026-03-01 02:30 EST", "2026-03-08 03:30 EDT", "2026-03-15 02:30 EDT"]
