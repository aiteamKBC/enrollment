"""Link approved lecture excuses to coach-completed catch-up sessions."""
import json
from collections import defaultdict

from django.db import connections, transaction, DatabaseError, router
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from login.permissions import learner_self_or_admin


def apply_recovery_to_rows(rows):
    from curriculum_api.session_results import apply_recovery
    grouped = defaultdict(list)
    for row in rows:
        if row.get('occurrence_id'):
            grouped[row['occurrence_id']].append(row)
    sessions = [{'id': key, 'attendance': [{'email': str(row['learner_email']).strip().casefold(),
        'status': row['attendance_status'], 'seconds': row.get('attended_seconds', 0),
        'attendance': 1 if row['attendance_status'] == 'present' else 0,
        'excused': False, 'catchupCompleted': False} for row in group]} for key, group in grouped.items()]
    apply_recovery(sessions)
    for session in sessions:
        for row, person in zip(grouped[session['id']], session['attendance']):
            row['catchup_completed'] = person['catchupCompleted']
            row['excused'] = person['excused']
            row['raw_attendance_status'] = person['rawStatus']
            row['effective_attendance_status'] = person['effectiveStatus']
            row['effective_attendance'] = person['effectiveAttendance']
            row['final_outcome'] = person['finalOutcome']
            row['excuse_status'] = person['excuseStatus']
            row['recovery_status'] = person['recoveryStatus']
            if person['catchupCompleted']:
                row['absence_reason'] = (
                    'Excused absence; attended approved alternative session'
                    if person.get('recoveryType') == 'alternative'
                    else 'Excused absence; completed coach catch-up'
                )
            elif person['excused']:
                row['absence_reason'] = (
                    'Excused absence; alternative session scheduled'
                    if person.get('recoveryType') == 'alternative'
                    else 'Excused absence; coach catch-up required'
                )
    return rows


def refresh_catchup_attendance(event_key):
    """Queue refreshed effective results without rewriting raw Teams attendance."""
    from coach_api.models import CoachAbsenceReport, CoachCalendarEvent
    from .absence_reports import _kbc_attendance_report_id
    event = CoachCalendarEvent.objects.filter(event_key=event_key, event_type='catch-up', status='completed').first()
    if not event:
        return
    # A completed catch-up recovers the lecture only if the learner attended it.
    from .catchup_outcomes import learner_attended_catchup
    if not learner_attended_catchup(event_key):
        return
    reports = list(CoachAbsenceReport.objects.filter(catchup_event_key=event_key, status='approved'))
    with transaction.atomic(), connections['default'].cursor() as cursor:
        from curriculum_api.live_session_absences import complete_reported_catchup
        from curriculum_api.models import LiveSessionAbsence
        complete_reported_catchup(
            database=router.db_for_write(LiveSessionAbsence) or 'default',
            event_key=event_key,
            source_learner_ids=[report.learner_id for report in reports],
        )
        for report in reports:
            cursor.execute('''SELECT d.learner_id,d.session_id FROM "Learner".learner_attendance_details d
                JOIN "Learner".learners l ON l.id=d.learner_id
                WHERE l.enrolment_id=%s AND lower(btrim(l.email))=%s AND d.source='microsoft_teams' ''',
                [report.learner_id, str(event.learner_email or '').strip().casefold()])
            for attendance_learner_id, session_id in cursor.fetchall():
                if _kbc_attendance_report_id(f'{report.learner_id}:teams:{session_id}') != report.attendance_id:
                    continue
                cursor.execute('''UPDATE "Learner".learner_attendance_details
                    SET catchup_completed=true,
                        absence_reason='Excused absence; completed coach catch-up',updated_at=now()
                    WHERE learner_id=%s AND session_id=%s AND source='microsoft_teams' ''',
                    [attendance_learner_id, session_id])
                cursor.execute('''INSERT INTO curriculum.session_result_jobs(live_session_id)
                    SELECT live_session_id FROM curriculum.live_session_occurrences WHERE id=%s
                    ON CONFLICT(live_session_id) DO UPDATE
                    SET state=CASE WHEN session_result_jobs.state='running' THEN 'running' ELSE 'queued' END,
                        requested_at=now(),next_attempt_at=now(),attempts=0''', [session_id])


@require_POST
@csrf_protect
@learner_self_or_admin(kwarg='learner_id')
def link_catchup(request, kind, learner_id):
    from .absence_reports import _source_learner, RecoveryPlanError
    from .models import LearnerProfile
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError()
        report_id = int(payload.get('reportId'))
        event_key = str(payload.get('eventKey') or '')
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Select an absence report and a catch-up booking.'}, status=400)
    learner = _source_learner(kind, learner_id)
    if not learner:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    try:
        mirror = LearnerProfile.objects.filter(enrolment_id=learner_id).first()
        link_report_to_catchup(learner, mirror, learner_id, report_id, event_key,
                               enforce_change_cutoff=learner_change(request))
        return JsonResponse({'saved': True, 'message': LINKED_MESSAGE})
    except ReportNotFound:
        return JsonResponse({'error': 'Absence report not found.'}, status=404)
    except RecoveryPlanError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except DatabaseError:
        return JsonResponse({'error': 'Catch-up could not be linked. Please retry.'}, status=503)


LINKED_MESSAGE = 'Catch-up linked. The absence is made up once your coach confirms the catch-up is completed.'


class ReportNotFound(Exception):
    pass


def check_report_can_take_catchup(learner_id, report_id, *, lock=False, enforce_change_cutoff=False, event_key=''):
    """The learner's live absence report a catch-up may (re)make up, or raise.

    With ``enforce_change_cutoff`` (a learner's own change), a booked catch-up
    starting within the cut-off cannot be replaced by another one.
    """
    from coach_api.models import CoachAbsenceReport, CoachCalendarEvent
    from .absence_reports import RecoveryPlanError
    from .catchup_outcomes import learner_attended_catchup
    from .coach_availability import CATCHUP_CHANGE_CLOSED_MESSAGE, catchup_change_closed
    query = CoachAbsenceReport.objects.select_for_update() if lock else CoachAbsenceReport.objects
    report = query.filter(pk=report_id, learner_id=learner_id).first()
    if not report or report.status == 'declined':
        raise ReportNotFound()
    # A completed catch-up the learner missed can be replaced by a new booking.
    if (report.catchup_event_key
            and CoachCalendarEvent.objects.filter(event_key=report.catchup_event_key, status='completed').exists()
            and learner_attended_catchup(report.catchup_event_key)):
        raise RecoveryPlanError('A completed catch-up cannot be replaced.')
    if enforce_change_cutoff and report.catchup_event_key and report.catchup_event_key != event_key:
        current = CoachCalendarEvent.objects.filter(
            event_key=report.catchup_event_key, status=CoachCalendarEvent.STATUS_SCHEDULED,
        ).only('scheduled_date', 'scheduled_time').first()
        if current and catchup_change_closed(current.scheduled_date, current.scheduled_time):
            raise RecoveryPlanError(CATCHUP_CHANGE_CLOSED_MESSAGE)
    return report


def learner_change(request):
    """Whether the request is the learner's own change (staff may change a catch-up at any time)."""
    return getattr(getattr(request, 'account', None), 'role', '') not in {'admin', 'staff'}


def link_report_to_catchup(learner, mirror, learner_id, report_id, event_key, *, enforce_change_cutoff=False):
    """Make ``event_key`` the catch-up that recovers this absence report (one transaction)."""
    from coach_api.models import CoachAbsenceReport
    from .absence_reports import _catchup_booking
    with transaction.atomic():
        report = check_report_can_take_catchup(learner_id, report_id, lock=True,
                                               enforce_change_cutoff=enforce_change_cutoff, event_key=event_key)
        replaced_key = report.catchup_event_key if report.catchup_event_key != event_key else ''
        _catchup_booking(learner, mirror, event_key, report.session_date, lock=True, for_report_id=report.pk)
        report.catchup_event_key = event_key
        report.recovery_method = 'catch-up'
        # A learner's recovery choice needs no coach approval, as when reporting.
        report.status = CoachAbsenceReport.STATUS_APPROVED
        report.save(update_fields=['catchup_event_key', 'recovery_method', 'status', 'updated_at'])
        _record_linked_catchup(learner_id, report.attendance_id, event_key)
    if replaced_key:
        _cancel_replaced_catchup(replaced_key)
    return report


def _cancel_replaced_catchup(event_key):
    """The catch-up a changed plan no longer uses is cancelled, so the coach is not left with it.

    Only a booking that has not happened yet and makes up no other lecture is cancelled.
    The new link is already saved; a failure here is logged and the old booking stays.
    """
    import logging
    from coach_api.models import CoachAbsenceReport, CoachCalendarEvent
    from .calendar import cancel_booking
    try:
        booking = CoachCalendarEvent.objects.filter(
            event_key=event_key, event_type='catch-up',
            status__in=[CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_NOT_SCHEDULED],
        ).first()
        if booking is None or CoachAbsenceReport.objects.filter(catchup_event_key=event_key).exclude(
                status=CoachAbsenceReport.STATUS_DECLINED).exists():
            return
        cancel_booking(booking)
    except Exception:  # noqa: BLE001 - the new catch-up is already linked
        logging.getLogger(__name__).exception('Could not cancel replaced catch-up %s', event_key)


def _record_linked_catchup(learner_id, attendance_id, event_key):
    """Point the reported live-session absence at the newly linked catch-up.

    Completion is matched on the absence ledger (see complete_reported_catchup),
    so a catch-up linked after the report must be recorded there as well.
    KBC register absences have no ledger row and are left unchanged.
    """
    from curriculum_api.live_session_absences import recovery_status_for
    from curriculum_api.models import LiveSessionAbsence, LiveSessionLearnerAttendance
    from .absence_reports import _kbc_attendance_report_id, _native_absence_occurrence_id, _source_learner
    from .attendance_lectures import read_native_occurrences, report_id
    database = router.db_for_write(LiveSessionAbsence) or 'default'
    absences = LiveSessionAbsence.objects.using(database)
    rows = list(absences.filter(source_learner_id=learner_id)
                .values('id', 'occurrence_id', 'learner_profile_id', 'source_kind'))
    matches = [
        row for row in rows
        if _kbc_attendance_report_id(f"{learner_id}:teams:{row['occurrence_id']}") == attendance_id
    ]
    if rows and not matches:
        # Shared-module reports retain the display identity used by the learner
        # and coach. Their ledger is attached to the stored source occurrence.
        kind = rows[0].get('source_kind') or 'apprenticeship'
        source = _source_learner(kind, learner_id)
        scheduled = read_native_occurrences(source) if source is not None else []
        occurrence_ids = {
            _native_absence_occurrence_id(row) for row in scheduled
            if report_id(row) == attendance_id
        } - {''}
        if len(occurrence_ids) == 1:
            matches = [row for row in rows if row['occurrence_id'] in occurrence_ids]
    status, now = recovery_status_for('catch-up'), timezone.now()
    for row in matches:
        absences.filter(pk=row['id']).update(
            recovery_status=status, recovery_method='catch-up', recovery_reference=event_key, updated_at=now,
        )
        LiveSessionLearnerAttendance.objects.using(database).filter(
            occurrence_id=row['occurrence_id'],
            learner_profile_id=row['learner_profile_id'],
            attendance_status=LiveSessionLearnerAttendance.STATUS_ABSENT,
        ).update(recovery_status=status, updated_at=now)
    return len(matches)
