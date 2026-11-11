"""Versionable annual intake calendar primitives used by cohort assignment."""
from datetime import date
import re


def cohort_assignment_window(cohort):
    """An explicit Cohort's assignment window, independent of delivery dates.

    No identity is inferred from a label or a legacy intake code.
    """
    period = cohort.get('assignmentPeriod')
    if not isinstance(period, dict):
        return None
    try:
        year = period.get('year')
        if year is None:
            # Compatibility for period snapshots created before year metadata.
            # This extracts a year, never resolves a Cohort identity by name.
            years = re.findall(r'(?<!\d)(\d{4})(?!\d)', str(cohort.get('name', '')))
            if len(years) != 1:
                raise ValueError
            year = int(years[0])
        if type(year) is not int or not 1000 <= year <= 9999:
            raise ValueError
        values = [period[k] for k in ('startMonth', 'startDay', 'endMonth', 'endDay')]
        if any(type(v) is not int for v in values):
            raise ValueError
        sm, sd, em, ed = values
        # Validate against a leap year, then the actual cohort year as well.
        date(2024, sm, sd); date(2024, em, ed)
        end = date(year, em, ed)
        start = date(year - ((sm, sd) > (em, ed)), sm, sd)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Cohort assignment periods need a valid year (for example Feb 2027) and valid month/day boundaries.') from exc
    return start, end

DEFAULT_INTAKES = [
    {"code": "Feb", "name": "February", "startMonth": 10, "startDay": 15, "endMonth": 2, "endDay": 15, "active": True},
    {"code": "Jun", "name": "June", "startMonth": 2, "startDay": 16, "endMonth": 6, "endDay": 15, "active": True},
    {"code": "Oct", "name": "October", "startMonth": 6, "startDay": 16, "endMonth": 10, "endDay": 14, "active": True},
]


def validate_calendar(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError("At least one active intake is required.")
    codes = set()
    for item in entries:
        if not isinstance(item, dict) or not item.get("code") or item["code"] in codes:
            raise ValueError("Intake codes must be unique.")
        codes.add(item["code"])
        for key in ("startMonth", "startDay", "endMonth", "endDay"):
            if type(item.get(key)) is not int or item[key] < 1:
                raise ValueError(f"{key} must be a positive integer.")
        try:
            date(2024, item["startMonth"], item["startDay"])
            date(2024, item["endMonth"], item["endDay"])
        except ValueError as exc:
            raise ValueError(f"Invalid date in intake {item['code']}.") from exc
    if not any(item.get("active", True) for item in entries):
        raise ValueError("At least one active intake is required.")
    for ordinal in range(1, 367):
        day = date(2024, 1, 1) + __import__('datetime').timedelta(days=ordinal - 1)
        value = (day.month, day.day)
        matches = []
        for item in entries:
            if not item.get('active', True):
                continue
            start, end = (item['startMonth'], item['startDay']), (item['endMonth'], item['endDay'])
            if (start <= end and start <= value <= end) or (start > end and (value >= start or value <= end)):
                matches.append(item['code'])
        if len(matches) > 1:
            raise ValueError(f"Intake windows overlap on {day.isoformat()}: {', '.join(matches)}.")
    return entries


def resolve_intake(completed_on, entries=None):
    entries = validate_calendar(entries or DEFAULT_INTAKES)
    value = (completed_on.month, completed_on.day)
    active = [x for x in entries if x.get("active", True)]
    for item in active:
        start, end = (item["startMonth"], item["startDay"]), (item["endMonth"], item["endDay"])
        if start <= end and start <= value <= end:
            return item["code"], completed_on.year
        if start > end and (value >= start or value <= end):
            return item["code"], completed_on.year + (1 if value >= start else 0)
    raise ValueError("The intake calendar has a gap for this date.")
