"""Pure communication policy. No database, network, or notification side effects."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CATEGORIES = ("cohort_messages", "private_messages", "meetings", "attendance", "assignments", "tasks")
LONDON = ZoneInfo("Europe/London")

def default_preferences():
    return {category: {"inApp": True, "push": False} for category in CATEGORIES}

def validate_preferences(value):
    if not isinstance(value, dict) or set(value) != set(CATEGORIES):
        raise ValueError("Choose preferences for each notification category.")
    for channels in value.values():
        if not isinstance(channels, dict) or set(channels) != {"inApp", "push"}:
            raise ValueError("Each category needs in-app and phone preferences.")
        if any(type(v) is not bool for v in channels.values()):
            raise ValueError("Notification choices must be true or false.")
    return value

def aware(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required.")
    return value

def reminder_plan(event):
    """Input is a server-owned, current LMS event, never a device claim.

    Completed/submitted work and excused/cancelled meetings produce no alerts.
    Unknown attendance gets neutral wording; this function does not mark absence.
    """
    kind = event["kind"]
    key = str(event["key"])
    if event.get("cancelled") or event.get("completed") or event.get("submitted"):
        return []
    if kind in {"meeting", "session"}:
        if event.get("authorisedAbsence"):
            return []
        start = aware(event["startsAt"])
        # Same UK clock time on the previous calendar day (including DST).
        previous = start.astimezone(LONDON) - timedelta(days=1)
        candidates = [
            ("day-before", "meetings", previous, "Your meeting is tomorrow."),
            ("15-before", "meetings", start - timedelta(minutes=15), "Your meeting starts in 15 minutes."),
        ]
        if event.get("attended") is not True:
            candidates.append(("15-after", "attendance", start + timedelta(minutes=15),
                               "Your session has started. Attendance is not yet confirmed."))
    elif kind in {"assignment", "task"}:
        due = aware(event["dueAt"]).astimezone(LONDON)
        category = "assignments" if kind == "assignment" else "tasks"
        candidates = [(str(days) + "-days-before", category, due - timedelta(days=days),
                       ("Your assignment" if kind == "assignment" else "Your task") +
                       (" is due tomorrow." if days == 1 else " is due in two days."))
                      for days in (2, 1)]
    else:
        raise ValueError("Unsupported reminder type.")
    return [{"key": key + ":" + stage + ":" + instant.isoformat(),
             "sourceKey": key, "category": category,
             "dueAt": instant.astimezone(timezone.utc), "body": body}
            for stage, category, instant, body in candidates]

def eligible_now(item, event, now):
    """Re-evaluate immediately before dispatch, so LMS changes suppress old jobs."""
    now = aware(now)
    fresh = {row["key"]: row for row in reminder_plan(event)}
    current = fresh.get(item["key"])
    return bool(current and current["dueAt"] <= now < current["dueAt"] + timedelta(minutes=10))
