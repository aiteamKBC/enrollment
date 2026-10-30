"""Persistence helpers for the curriculum live-session absence ledger."""

from django.utils import timezone

from .models import LiveSessionAbsence, LiveSessionLearnerAttendance, LiveSessionOccurrence


def recovery_status_for(method: str) -> str:
    method = str(method or '').strip().casefold()
    if method == 'catch-up':
        return LiveSessionAbsence.RECOVERY_CATCHUP_BOOKED
    if method:
        return LiveSessionAbsence.RECOVERY_REQUESTED
    return LiveSessionAbsence.RECOVERY_NONE


def record_reported_absence(
    *,
    database: str,
    occurrence_id: str,
    learner_profile_id: int,
    source_kind: str,
    source_learner_id: int,
    learner_email: str,
    learner_name: str,
    recovery_method: str,
    recovery_reference: str = '',
):
    """Create/update the learner-owned report without changing Teams evidence."""

    occurrence = LiveSessionOccurrence.objects.using(database).only('id').get(
        pk=occurrence_id,
    )
    saved = LiveSessionAbsence.objects.using(database).update_or_create(
        occurrence_id=occurrence.id,
        learner_profile_id=learner_profile_id,
        defaults={
            'source_kind': str(source_kind or '').strip(),
            'source_learner_id': source_learner_id,
            'learner_email': str(learner_email or '').strip().casefold(),
            'learner_name': str(learner_name or '').strip(),
            'recovery_status': recovery_status_for(recovery_method),
            'recovery_method': str(recovery_method or '').strip(),
            'recovery_reference': str(recovery_reference or '').strip(),
            'reported_at': timezone.now(),
        },
    )
    LiveSessionLearnerAttendance.objects.using(database).filter(
        occurrence_id=occurrence.id,
        learner_profile_id=learner_profile_id,
        attendance_status=LiveSessionLearnerAttendance.STATUS_ABSENT,
    ).update(
        recovery_status=recovery_status_for(recovery_method),
        updated_at=timezone.now(),
    )
    return saved


def release_cancelled_catchup(*, database: str, event_key: str) -> int:
    """A cancelled catch-up no longer makes the absence up: back to recovery requested.

    Only bookings still pending are released; a completed catch-up is history.
    """

    event_key = str(event_key or '').strip()
    if not event_key:
        return 0
    rows = list(
        LiveSessionAbsence.objects.using(database).filter(
            recovery_method='catch-up', recovery_reference=event_key,
            recovery_status=LiveSessionAbsence.RECOVERY_CATCHUP_BOOKED,
        ).values_list('id', 'occurrence_id', 'learner_profile_id')
    )
    now = timezone.now()
    for row_id, occurrence_id, learner_profile_id in rows:
        LiveSessionAbsence.objects.using(database).filter(pk=row_id).update(
            recovery_status=LiveSessionAbsence.RECOVERY_REQUESTED, updated_at=now,
        )
        LiveSessionLearnerAttendance.objects.using(database).filter(
            occurrence_id=occurrence_id, learner_profile_id=learner_profile_id,
            attendance_status=LiveSessionLearnerAttendance.STATUS_ABSENT,
            recovery_status=LiveSessionAbsence.RECOVERY_CATCHUP_BOOKED,
        ).update(recovery_status=LiveSessionAbsence.RECOVERY_REQUESTED, updated_at=now)
    return len(rows)


def complete_reported_catchup(*, database: str, event_key: str, source_learner_ids):
    """Mark approved linked catch-ups complete without rewriting Teams presence."""

    learner_ids = [int(value) for value in source_learner_ids if value is not None]
    if not event_key or not learner_ids:
        return 0
    queryset = LiveSessionAbsence.objects.using(database).filter(
        recovery_method='catch-up',
        recovery_reference=event_key,
        source_learner_id__in=learner_ids,
    )
    absences = list(queryset.values('occurrence_id', 'learner_profile_id'))
    if not absences:
        return 0
    now = timezone.now()
    queryset.update(
        recovery_status=LiveSessionAbsence.RECOVERY_COMPLETED,
        updated_at=now,
    )
    for absence in absences:
        LiveSessionLearnerAttendance.objects.using(database).filter(
            occurrence_id=absence['occurrence_id'],
            learner_profile_id=absence['learner_profile_id'],
            attendance_status=LiveSessionLearnerAttendance.STATUS_ABSENT,
        ).update(
            recovery_status=LiveSessionLearnerAttendance.RECOVERY_COMPLETED,
            updated_at=now,
        )
    return len(absences)
