"""Schedule specification and next-occurrence computation. Pure; DST rules live here."""

from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_LOOKAHEAD_DAYS = 16


class ScheduleSpec(BaseModel):
    """Daily or weekly at a local wall-clock time. Nothing more frequent exists by design."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["daily", "weekly"]
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[Weekday] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def _days_match_kind(self) -> "ScheduleSpec":
        if self.kind == "daily" and self.days:
            raise ValueError("daily schedules do not take days")
        if self.kind == "weekly" and (not self.days or len(set(self.days)) != len(self.days)):
            raise ValueError("weekly schedules need one or more distinct days")
        return self

    @property
    def local_time(self) -> clock_time:
        hour, minute = self.time.split(":")
        return clock_time(int(hour), int(minute))


def parse_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"Unknown timezone: {name!r}") from exc


def _resolve(local_date: date, at: clock_time, zone: ZoneInfo) -> datetime:
    """UTC instant for a local date and time.

    A local time inside a spring-forward gap resolves with the pre-transition offset, which lands
    one gap-length later (e.g. 02:30 becomes 03:30). A time that occurs twice (fall-back) resolves
    to its first occurrence (fold=0), so it runs once.
    """
    return datetime.combine(local_date, at).replace(tzinfo=zone, fold=0).astimezone(UTC)


def next_occurrence(spec: ScheduleSpec, zone: ZoneInfo, after: datetime) -> datetime:
    """First occurrence strictly after `after`, as an aware UTC datetime."""
    if after.tzinfo is None:
        raise ValueError("`after` must be timezone-aware")
    after_utc = after.astimezone(UTC)
    first_day = after_utc.astimezone(zone).date() - timedelta(days=1)
    wanted = set(spec.days) if spec.kind == "weekly" else set(WEEKDAYS)
    candidates = []
    for offset in range(_LOOKAHEAD_DAYS):
        day = first_day + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] not in wanted:
            continue
        instant = _resolve(day, spec.local_time, zone)
        if instant > after_utc:
            candidates.append(instant)
    return min(candidates)


MAX_CATCH_UP_STEPS = 1000


def latest_due_occurrence(
    spec: ScheduleSpec, zone: ZoneInfo, first: datetime, now: datetime
) -> tuple[datetime, int]:
    """The latest occurrence at or before `now`, starting from the stored `first` occurrence.

    Returns it together with how many earlier occurrences were skipped. Very old schedules are
    fast-forwarded instead of walked step by step.
    """
    occurrence, skipped = first, 0
    for _ in range(MAX_CATCH_UP_STEPS):
        following = next_occurrence(spec, zone, occurrence)
        if following > now:
            return occurrence, skipped
        occurrence, skipped = following, skipped + 1
    return next_occurrence(spec, zone, now - timedelta(days=1)), skipped
