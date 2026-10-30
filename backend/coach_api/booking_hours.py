"""Shared London working-hours policy for Review appointments."""

from datetime import datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo


REVIEW_TIME_ZONE = ZoneInfo("Europe/London")
REVIEW_DAY_START = time(9, 0)
REVIEW_DAY_END = time(17, 0)
REVIEW_BOOKING_ERROR = (
    "Review meetings must start at or after 09:00 and finish by 17:00 UK time "
    "(Europe/London). Choose another time or duration."
)
REVIEW_DURATION_ERROR = "Choose a supported Review duration between 15 and 480 minutes."


def validate_review_duration(duration_minutes):
    """Return a validated configurable Review duration, rejecting coercions."""
    if isinstance(duration_minutes, str) and re.fullmatch(r"[0-9]+", duration_minutes):
        duration_minutes = int(duration_minutes)
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        raise ValueError(REVIEW_DURATION_ERROR)
    if duration_minutes < 15 or duration_minutes > 480:
        raise ValueError(REVIEW_DURATION_ERROR)
    return duration_minutes


def validate_review_time_precision(value):
    """Reject seconds or client timezone offsets before a parser can truncate them."""
    if isinstance(value, datetime):
        parsed = value.timetz()
    elif isinstance(value, time):
        parsed = value
    else:
        try:
            parsed = time.fromisoformat(str(value or "").strip())
        except ValueError:
            return
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError(REVIEW_BOOKING_ERROR)


def validate_review_interval(scheduled_date, scheduled_time, duration_minutes):
    """Validate that a complete appointment fits in one London working day.

    The submitted date and time are London wall time, regardless of the caller's
    browser timezone or supplied offset. Duration is elapsed time, so the end is
    calculated in UTC before converting back through the selected date's GMT/BST.
    """
    duration = validate_review_duration(duration_minutes)
    if isinstance(scheduled_date, datetime):
        scheduled_date = scheduled_date.date()
    if not hasattr(scheduled_date, "year") or not isinstance(scheduled_time, time):
        raise ValueError(REVIEW_BOOKING_ERROR)
    if (scheduled_time.tzinfo is not None or scheduled_time.second or scheduled_time.microsecond):
        raise ValueError(REVIEW_BOOKING_ERROR)
    if scheduled_time < REVIEW_DAY_START or scheduled_time >= REVIEW_DAY_END:
        raise ValueError(REVIEW_BOOKING_ERROR)
    local_start = datetime.combine(scheduled_date, scheduled_time, REVIEW_TIME_ZONE)
    utc_start = local_start.astimezone(timezone.utc)
    local_end = (utc_start + timedelta(minutes=duration)).astimezone(REVIEW_TIME_ZONE)
    if local_end.date() != scheduled_date or local_end.time().replace(tzinfo=None) > REVIEW_DAY_END:
        raise ValueError(REVIEW_BOOKING_ERROR)
    return local_start, local_end
