"""Dashboard-only wire contract; no database or meeting mutations."""

from datetime import date
from .weeks import work_week


# Verified consumers: DashboardMeetingActions, slidesTargetFromEvent,
# eventPeriodLabel, sortEvents, and buildTimetableFocusState.
ACTION_FIELDS = ("eventKey", "enrolmentId", "reviewInstanceId", "reviewTemplateId", "sequence",
                 "importedReviewType", "reviewTypeCode", "reviewTypeName")
DISPLAY_FIELDS = ("startHour", "isTimeEstimated", "timeLabel", "location")
INPUT_FIELDS = ("id", "source", "type", "date", "scheduledDate", "targetDate", "scheduledTime",
                "durationMinutes", "learnerId", "learner", "title", "programme", "cohort", "group",
                "status", "meetingLink") + ACTION_FIELDS + DISPLAY_FIELDS


def next_work_week(today):
    return work_week(today, offset=1)


def upcoming_meetings(payload, start, end):
    events = []
    for event in (payload.get("meetings") or {}).get("events") or []:
        display_date = event.get("scheduledDate") or event.get("date") or event.get("targetDate")
        try:
            day = date.fromisoformat(str(display_date).split("T")[0])
        except ValueError:
            continue
        if not start <= day <= end:
            continue
        source = event.get("source")
        kind = {"mcr": "mcm", "progress-review": "progress-review", "catch-up": "catch-up",
                "live-session": "live-session"}.get(source)
        if kind is None:
            continue
        clock_time = (event.get("scheduledTime") or "")[:5] or None
        if kind == "live-session" and not clock_time and not event.get("isTimeEstimated") and event.get("startHour") is not None:
            minutes = round(float(event["startHour"]) * 60) % (24 * 60)
            clock_time = f"{minutes // 60:02d}:{minutes % 60:02d}"
        row = {"id": event.get("id"), "type": kind, "date": day.isoformat(),
               "time": clock_time,
               "durationMinutes": event.get("durationMinutes") or 60,
               "title": event.get("title"), "programme": event.get("programme"),
               "group": event.get("group"), "status": event.get("status"),
               "meetingLink": event.get("meetingLink") or None}
        if kind == "live-session":
            row["cohort"] = event.get("cohort")
        else:
            row["learner"] = {"id": event.get("learnerId"), "name": event.get("learner") or event.get("title")}
        extras = (("eventKey",) if kind == "live-session" else ACTION_FIELDS) + ("startHour", "isTimeEstimated")
        # Live labels may contain a full range. Unbooked imported reviews can
        # have a display label without a scheduled clock time.
        if kind == "live-session" or not clock_time:
            extras += ("timeLabel",)
        if kind == "live-session":
            extras += ("location",)
        row.update({key: event[key] for key in extras if key in event})
        events.append(row)
    return {"meetings": {"range": {"from": start.isoformat(), "to": end.isoformat()}, "events": events},
            "errors": {"meetings": payload["errors"]["meetings"]} if (payload.get("errors") or {}).get("meetings") else {}}
