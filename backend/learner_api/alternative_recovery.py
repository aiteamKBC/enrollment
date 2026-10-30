"""Cohort-linked alternative delivery recovery.

Curriculum owns the delivery facts.  A cohort may have parallel groups with
one active Teams series per group; matching occurrence numbers in that
unambiguous layout are the same cohort delivery slot.  If a group has more
than one active series, this module deliberately returns no alternative: the
repository has no stronger authored-component relationship to choose safely.
"""
from collections import defaultdict
import re
from datetime import timezone as datetime_timezone

from django.db.models import Q
from django.utils import timezone

from coach_api.models import CoachAbsenceReport
from curriculum_api.models import (
    LiveSession,
    LiveSessionOccurrence,
    ModuleAuthoringModule,
)

from .models import LearnerProfile


ALTERNATIVE_METHOD = "alternative"
ALTERNATIVE_KEY_PREFIX = "alternative:"
INACTIVE_STATUSES = {"cancelled", "canceled", "deleted", "failed", "superseded"}


def alternative_event_key(occurrence_id: str) -> str:
    return f"{ALTERNATIVE_KEY_PREFIX}{str(occurrence_id or '').strip()}"


def alternative_occurrence_id(event_key: str | None) -> str:
    value = str(event_key or "").strip()
    return value[len(ALTERNATIVE_KEY_PREFIX):] if value.startswith(ALTERNATIVE_KEY_PREFIX) else ""


def _active(value) -> bool:
    return str(value or "").strip().casefold() not in INACTIVE_STATUSES


def _module_key(value) -> str:
    """Compare copied deliveries without depending on their generated IDs.

    The curriculum UI appends ``copy`` when it creates the delivery for a
    second group, but the module and its session numbers still represent the
    same authored course.  The database does not retain a module-level source
    identifier for that copy, so ignore that generated suffix only at the end
    of the title.  Groups that meet on different days also name their copy by
    the weekday ("Martech - Thur" / "Martech - Fri"), so a trailing weekday is
    ignored as well; matching stays limited to one programme and cohort.
    """
    key = " ".join(str(value or "").split()).casefold()
    key = key[:-5].rstrip() if key.endswith(" copy") else key
    return _WEEKDAY_SUFFIX.sub("", key).rstrip()


# " - Thur", " (Fri)", " Monday" ... at the end of a module title.
_WEEKDAY_SUFFIX = re.compile(
    r"(?:\s*[-–—]\s*|\s+\(?)(mon(day)?|tue(s(day)?)?|wed(nesday)?|thu(r(s(day)?)?)?|fri(day)?|sat(urday)?|sun(day)?)\)?$"
)


def _matching_alternative_series(original_session, original_module, modules, sessions):
    """Resolve one unambiguous active series per other group for this module.

    A cohort can contain many modules and every module can have its own Teams
    series. Grouping every series only by group made an unrelated module with
    the same occurrence number look like a recovery session. Module copies have
    independent catalogue IDs, so their normalized authored title is the shared
    identity available across group deliveries.
    """
    original_module_id = str(original_module.module_catalogue_id)
    original_group_id = str(original_module.group_id)
    module_key = _module_key(original_module.title or original_session.module_title)
    if not module_key:
        return {}

    original_active = [
        session for session in sessions
        if str(session.module_catalogue_id or "") == original_module_id and _active(session.status)
    ]
    if len(original_active) != 1 or original_active[0].id != original_session.id:
        return {}

    candidate_modules = {
        str(module.module_catalogue_id): module
        for module in modules
        if str(module.group_id or "") != original_group_id
        and _module_key(module.title) == module_key
    }
    active_by_group = defaultdict(list)
    for session in sessions:
        module = candidate_modules.get(str(session.module_catalogue_id or ""))
        if module is not None and _active(session.status):
            active_by_group[str(module.group_id)].append((module, session))

    return {
        group_id: pairs[0]
        for group_id, pairs in active_by_group.items()
        if len(pairs) == 1
    }


def _local(value):
    return timezone.localtime(value) if value and timezone.is_aware(value) else value


def _future_instant(value, now=None) -> bool:
    """Compare native UTC timestamps with Django's aware clock safely."""
    if not value:
        return False
    instant = (
        timezone.make_aware(value, datetime_timezone.utc)
        if timezone.is_naive(value)
        else value
    )
    reference = now or timezone.now()
    reference = (
        timezone.make_aware(reference, datetime_timezone.utc)
        if timezone.is_naive(reference)
        else reference
    )
    return instant > reference


def _usable_module(module) -> bool:
    return (module is not None and not module.is_programme_deleted
            and all((module.programme_id, module.cohort_id, module.group_id)))


def eligible_alternative_occurrences(
    original_occurrence_id: str,
    *,
    at=None,
    database: str = "enrolment",
) -> list[dict]:
    """Return future parallel-group occurrences for one exact occurrence.

    `at` is the apology time.  This intentionally allows an alternative before
    or after the original lecture, provided the alternative has not started.
    """
    return eligible_alternatives_by_occurrence(
        [original_occurrence_id], at=at, database=database,
    ).get(str(original_occurrence_id), [])


def eligible_alternatives_by_occurrence(
    original_occurrence_ids,
    *,
    at=None,
    database: str = "enrolment",
) -> dict[str, list[dict]]:
    return _alternatives_by_occurrence(original_occurrence_ids, at=at, database=database)


def completed_alternatives_by_occurrence(original_occurrence_ids, *, at=None, database="enrolment"):
    """Equivalent completed deliveries; scheduling alone grants no attendance."""
    return _alternatives_by_occurrence(
        original_occurrence_ids, at=at, database=database, completed=True,
    )


def _alternatives_by_occurrence(original_occurrence_ids, *, at=None, database="enrolment", completed=False):
    """``eligible_alternative_occurrences`` for many occurrences in a few queries.

    A learner's absence form lists every missed lecture with its alternatives;
    asking per lecture cost several round trips each (minutes for a long plan).
    The same rules are applied to rows read once.
    """
    ids = [str(value) for value in dict.fromkeys(original_occurrence_ids) if value]
    result = {occurrence_id: [] for occurrence_id in ids}
    if not ids:
        return result
    now = at or timezone.now()

    occurrences = {
        occurrence.id: occurrence
        for occurrence in LiveSessionOccurrence.objects.using(database).filter(pk__in=ids)
        if _active(occurrence.status)
    }
    sessions = {
        session.id: session
        for session in LiveSession.objects.using(database).filter(
            pk__in={occurrence.live_session_id for occurrence in occurrences.values()})
        if _active(session.status)
    }
    first_module = {}
    for module in (ModuleAuthoringModule.objects.using(database)
                   .filter(module_catalogue_id__in={session.module_catalogue_id for session in sessions.values()},
                           deleted_at__isnull=True)
                   .order_by("pk")):
        first_module.setdefault(str(module.module_catalogue_id), module)

    contexts = {}
    for occurrence_id in ids:
        occurrence = occurrences.get(occurrence_id)
        session = sessions.get(occurrence.live_session_id) if occurrence else None
        module = first_module.get(str(session.module_catalogue_id)) if session else None
        if _usable_module(module) and occurrence.session_number is not None:
            contexts[occurrence_id] = (occurrence, session, module)
    if not contexts:
        return result

    # Each cohort's modules and series, read once however many lectures share it.
    cohort_rows = {}
    for cohort in {(module.programme_id, module.cohort_id) for _o, _s, module in contexts.values()}:
        modules = list(
            ModuleAuthoringModule.objects.using(database)
            .filter(programme_id=cohort[0], cohort_id=cohort[1], deleted_at__isnull=True, is_programme_deleted=False)
            .exclude(Q(group_id__isnull=True) | Q(group_id=""))
        )
        modules_by_id = {str(module.module_catalogue_id): module for module in modules}
        cohort_rows[cohort] = (modules, list(
            LiveSession.objects.using(database).filter(module_catalogue_id__in=list(modules_by_id))))

    matched = {}
    for occurrence_id, (occurrence, session, module) in contexts.items():
        modules, cohort_sessions = cohort_rows[(module.programme_id, module.cohort_id)]
        series = _matching_alternative_series(session, module, modules, cohort_sessions)
        if series:
            matched[occurrence_id] = series
    if not matched:
        return result

    series_ids = {pair[1].id for series in matched.values() for pair in series.values()}
    numbers = {contexts[occurrence_id][0].session_number for occurrence_id in matched}
    candidates = LiveSessionOccurrence.objects.using(database).filter(
        live_session_id__in=list(series_ids), session_number__in=list(numbers),
    )
    candidates = list(candidates.filter(
        **({"scheduled_end__lte": now} if completed else {"scheduled_start__gt": now})
    ).order_by("scheduled_start", "id"))
    for occurrence_id, series in matched.items():
        original = contexts[occurrence_id][0]
        session_context = {pair[1].id: (group_id, pair[0]) for group_id, pair in series.items()}
        for occurrence in candidates:
            if occurrence.session_number != original.session_number or not _active(occurrence.status):
                continue
            group_id, module = session_context.get(occurrence.live_session_id, (None, None))
            session = series[group_id][1] if group_id in series else None
            if module is None or session is None:
                continue
            start, end = _local(occurrence.scheduled_start), _local(occurrence.scheduled_end)
            result[occurrence_id].append({
                **({"moduleCatalogueId": str(module.module_catalogue_id)} if completed else {}),
                "id": occurrence.id,
                "sessionId": f"teams:{occurrence.id}",
                "title": f"{session.module_title or module.title or 'Live session'} — Session {occurrence.session_number}",
                "dateIso": start.date().isoformat(),
                "startTime": start.strftime("%H:%M"),
                "endTime": end.strftime("%H:%M") if end else "",
                "groupId": str(module.group_id),
                "group": module.group_name or "",
                "cohortId": str(module.cohort_id),
                "cohort": module.cohort_name or "",
                "module": session.module_title or module.title or "",
            })
    return result


def validate_alternative_occurrence(
    original_occurrence_id: str,
    target_occurrence_id: str,
    *,
    at=None,
    database: str = "enrolment",
) -> dict | None:
    return next(
        (
            item
            for item in eligible_alternative_occurrences(
                original_occurrence_id, at=at, database=database,
            )
            if item["id"] == str(target_occurrence_id or "").strip()
        ),
        None,
    )


def alternative_target_details(
    event_key: str | None,
    *,
    include_join_url: bool = False,
    database: str = "enrolment",
) -> dict | None:
    occurrence_id = alternative_occurrence_id(event_key)
    if not occurrence_id:
        return None
    occurrence = LiveSessionOccurrence.objects.using(database).filter(pk=occurrence_id).first()
    if occurrence is None:
        return None
    session = LiveSession.objects.using(database).filter(pk=occurrence.live_session_id).first()
    if session is None:
        return None
    module = ModuleAuthoringModule.objects.using(database).filter(
        module_catalogue_id=session.module_catalogue_id,
        deleted_at__isnull=True,
    ).first()
    if module is None:
        return None
    start, end = _local(occurrence.scheduled_start), _local(occurrence.scheduled_end)
    data = {
        "id": occurrence.id,
        "title": f"{session.module_title or module.title or 'Live session'} — Session {occurrence.session_number}",
        "dateIso": start.date().isoformat(),
        "startTime": start.strftime("%H:%M"),
        "endTime": end.strftime("%H:%M") if end else "",
        "groupId": str(module.group_id or ""),
        "group": module.group_name or "",
        "cohortId": str(module.cohort_id or ""),
        "cohort": module.cohort_name or "",
        "status": occurrence.status,
    }
    if include_join_url and _active(occurrence.status) and _future_instant(occurrence.scheduled_end):
        data["joinUrl"] = occurrence.join_url or session.join_url or ""
    return data


def approved_alternative_guests(
    database: str = "enrolment", current_emails_by_enrolment: dict[int, str] | None = None,
    *, occurrence_ids=None,
) -> dict[str, set[str]]:
    """Current learner emails expected only for their approved target occurrence."""
    reports_query = CoachAbsenceReport.objects.filter(
        status=CoachAbsenceReport.STATUS_APPROVED,
        recovery_method=ALTERNATIVE_METHOD,
        catchup_event_key__startswith=ALTERNATIVE_KEY_PREFIX,
    )
    if occurrence_ids is not None:
        reports_query = reports_query.filter(catchup_event_key__in=[alternative_event_key(value) for value in occurrence_ids])
    if current_emails_by_enrolment is not None:
        if not current_emails_by_enrolment:
            return defaultdict(set)
        reports_query = reports_query.filter(learner_id__in=current_emails_by_enrolment)
    reports = list(reports_query.values("learner_id", "learner_email", "catchup_event_key"))
    if not reports:
        return defaultdict(set)
    current_emails = current_emails_by_enrolment
    if current_emails is None:
        current_emails = {
            profile.enrolment_id: str(profile.email or "").strip().casefold()
            for profile in LearnerProfile.objects.using(database).filter(
                enrolment_id__in=[report["learner_id"] for report in reports]
            ).only('enrolment_id', 'email')
        }
    guests = defaultdict(set)
    for report in reports:
        occurrence_id = alternative_occurrence_id(report["catchup_event_key"])
        email = current_emails.get(report["learner_id"]) or str(report["learner_email"] or "").strip().casefold()
        if occurrence_id and email:
            guests[occurrence_id].add(email)
    return guests
