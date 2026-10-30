"""Conservative placement for newly discovered Teams recordings."""

from datetime import datetime, timedelta, timezone


MAX_UNLINKED_START_OFFSET = timedelta(hours=12)


def _instant(value):
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
        except ValueError:
            return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def recording_matches_occurrence(recording, occurrence, *, linked_by_launch=False):
    """Keep rehearsals and unrelated meeting runs out of learner components.

    A recorded run that finished before the planned lecture is not delivery,
    even if someone opened that week's Join link early. For older runs without
    an LMS launch, require the recording to start near the planned time.
    Existing saved rows are left alone and can be reviewed separately.
    """
    scheduled = _instant((occurrence or {}).get('scheduled_start'))
    started = _instant((recording or {}).get('createdDateTime'))
    finished = _instant((recording or {}).get('endDateTime'))
    if not scheduled or not (started or finished):
        return False
    if finished and finished <= scheduled and (not started or started < scheduled):
        return False
    if linked_by_launch:
        return True
    return abs((started or finished) - scheduled) <= MAX_UNLINKED_START_OFFSET
