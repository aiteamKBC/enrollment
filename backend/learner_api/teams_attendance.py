from collections import defaultdict
from curriculum_api.session_results_policy import attendance_display_name, attendance_name_key, evidence_seconds
from datetime import datetime, timezone as datetime_timezone

from django.db import connections, DatabaseError, router, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from curriculum_api.models import (
    LiveSession,
    LiveSessionAttendance,
    LiveSessionAttendanceAlias,
    LiveSessionAttendanceIdentityLink,
    LiveSessionOccurrence,
    ModuleAuthoringModule,
)
from curriculum_api.live_session_register import upsert_live_session_register
from curriculum_api.attendance_link_policy import apply_reviewed_identities
from curriculum_api.attendance_link_store import identity_rules

from .models import LearnerProfile, LearnerTrainingPlanModule
from .projection_performance import projection_call, projection_operation
from .replaced_teams_attendance import REPLACED_ATTENDANCE_LATERAL_SQL


def _email(value) -> str:
    return str(value or "").strip().casefold()


def annotate_attendance_completion(components, learner_profile):
    """Read-only completion evidence, never an extra progress/hour ledger row.

    Verified Teams evidence respects authoring requirements. An explicit staff
    attendance decision also completes the activity, including those requirements.
    """
    clean = [{key: value for key, value in item.items()
              if key not in {'attendanceCompleted', 'attendanceCompletedAt'}} for item in components]
    profile_id = getattr(learner_profile, 'pk', None)
    candidates = [item for item in clean if
                  str(item.get('type') or '').strip().lower().replace('-', '_') == 'live_session'
                  and item.get('componentId') and item.get('moduleId')]
    if not profile_id or not candidates:
        return clean
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'''
            SELECT c.id,c.module_catalogue_id,r.last_leave_at
            FROM curriculum.components c
            JOIN curriculum.live_session_occurrences o
              ON o.id=coalesce(c.settings_json->>'teamsOccurrenceId',
                              c.settings_json #>> '{{legacySettings,teamsOccurrenceId}}')
            JOIN curriculum.live_sessions s ON s.id=o.live_session_id
              AND s.module_catalogue_id=c.module_catalogue_id
            JOIN curriculum.live_session_learner_attendance r
              ON r.occurrence_id=o.id AND r.live_session_id=s.id
              AND r.module_catalogue_id=c.module_catalogue_id
              AND r.learner_profile_id=%s
            {REPLACED_ATTENDANCE_LATERAL_SQL}
            WHERE c.id=ANY(%s) AND c.deleted_at IS NULL
              AND NOT coalesce(c.is_programme_deleted,false)
              AND lower(replace(c.type,'-','_'))='live_session'
              AND c.reflection_required IS FALSE
              AND c.tutor_validation_required IS FALSE
              AND NOT coalesce(c.workplace_evidence_required,false)
              AND r.attendance_status='present' AND r.attended_seconds>180
              AND coalesce(r.attendance_report_id,'')<>''
              AND r.attendance_report_id=CASE WHEN replaced_attendance.occurrence_id IS NOT NULL
                  THEN replaced_attendance.attendance_report_id ELSE o.attendance_report_id END
              AND o.scheduled_end<=CURRENT_TIMESTAMP
              AND lower(btrim(o.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
              AND lower(btrim(s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
        ''', [profile_id, list({item['componentId'] for item in candidates})])
        verified = {(str(cid), str(mid)): ended for cid, mid, ended in cursor.fetchall()}
    from .manual_attendance_completion import read_manual_completion
    manual = read_manual_completion(candidates, learner_profile)
    for key, (present, ended) in manual.items():
        if present:
            verified[key] = ended
        else:
            verified.pop(key, None)
    for item in candidates:
        key = (str(item.get('componentId') or ''), str(item.get('moduleId') or ''))
        if key in verified:
            item['attendanceCompleted'] = True
            ended = verified[key]
            item['attendanceCompletedAt'] = ended.isoformat() if ended else None
    return clean


def _canonical_attendance_email(module_ref, value, aliases_by_module) -> str:
    """Resolve a reviewed module-scoped alias without guessing by display name."""

    email = _email(value)
    return aliases_by_module.get(str(module_ref or '').strip(), {}).get(email, email)


def _attendance_alias_rows(database: str, module_refs):
    """Keep the existing attendance sync usable before the optional alias table is installed."""

    try:
        return list(
            LiveSessionAttendanceAlias.objects.using(database)
            .filter(module_catalogue_id__in=list(module_refs))
            .values_list('module_catalogue_id', 'alias_email', 'canonical_email')
        )
    except DatabaseError as error:
        cause = error.__cause__ or error
        if getattr(cause, 'sqlstate', None) != '42P01':
            raise
        return []


def _attendance_identity_link_rows(database: str, occurrence_ids):
    """Return optional staff-reviewed links for email-less Teams rows."""

    try:
        return list(
            LiveSessionAttendanceIdentityLink.objects.using(database)
            .filter(occurrence_id__in=list(occurrence_ids))
            .values_list('occurrence_id', 'attendance_row_id', 'canonical_email')
        )
    except DatabaseError as error:
        cause = error.__cause__ or error
        if getattr(cause, 'sqlstate', None) != '42P01':
            raise
        return []


def _attendance_review_rows(database, module_refs, occurrence_ids):
    """One scoped read for two optional reviewed-identity tables.

    Older schemas retain the independent readers and their missing-table rules.
    Never swallow a permission, transport or malformed-query failure.
    """
    try:
        with connections[database].cursor() as cursor:
            cursor.execute('''SELECT 'alias',module_catalogue_id::text,alias_email,canonical_email
                FROM curriculum.live_session_attendance_aliases WHERE module_catalogue_id=ANY(%s)
                UNION ALL
                SELECT 'link',occurrence_id::text,attendance_row_id::text,canonical_email
                FROM curriculum.live_session_attendance_identity_links WHERE occurrence_id=ANY(%s)''',
                [list(module_refs), list(occurrence_ids)])
            rows = cursor.fetchall()
    except DatabaseError as error:
        if getattr(error.__cause__ or error, 'sqlstate', None) != '42P01':
            raise
        return (_attendance_alias_rows(database, module_refs),
                _attendance_identity_link_rows(database, occurrence_ids))
    return ([tuple(row[1:]) for row in rows if row[0] == 'alias'],
            [tuple(row[1:]) for row in rows if row[0] == 'link'])


def _replaced_attendance_sources(database: str, occurrence_ids) -> dict[str, dict[str, str]]:
    """Destination occurrence -> source report behind its replaced join link."""

    occurrence_ids = sorted({str(value or '').strip() for value in occurrence_ids if str(value or '').strip()})
    if not occurrence_ids:
        return {}
    with connections[database].cursor() as cursor:
        cursor.execute(
            f'''
            SELECT o.id, replaced_attendance.occurrence_id,
                   replaced_attendance.attendance_report_id
            FROM curriculum.live_session_occurrences o
            JOIN curriculum.live_sessions s ON s.id=o.live_session_id
            {REPLACED_ATTENDANCE_LATERAL_SQL}
            WHERE o.id=ANY(%s)
              AND replaced_attendance.occurrence_id IS NOT NULL
            ''',
            [occurrence_ids],
        )
        return {
            str(destination_id): {
                'occurrence_id': str(source_id),
                'attendance_report_id': str(report_id or ''),
            }
            for destination_id, source_id, report_id in cursor.fetchall()
        }


def _unique_exact_name_email(display_name, expected_emails, learners_by_email):
    """Match only one exact full name; partial, reordered and ambiguous names stay unmatched."""

    key = attendance_name_key(display_name)
    if not key:
        return ''
    matches = {
        email for email in expected_emails
        if email in learners_by_email
        and attendance_name_key(getattr(learners_by_email[email], 'full_name', '')) == key
    }
    return next(iter(matches)) if len(matches) == 1 else ''


def _assigned_learner_emails_by_module(database: str, module_refs) -> dict[str, set[str]]:
    """Return the authored learner-plan roster for each delivery module.

    Teams invitations and attendance reports also contain organizers,
    presenters and other guests.  They are evidence of meeting participation,
    not evidence that the person is a learner assigned to this module.
    """

    wanted = {str(value or "").strip() for value in module_refs}
    wanted.discard("")
    result: dict[str, set[str]] = defaultdict(set)
    if not wanted:
        return result
    rows = (
        LearnerTrainingPlanModule.objects.using(database)
        .filter(module_ref__in=wanted)
        .values_list("module_ref", "learner__email")
    )
    for module_ref, email in rows:
        email_key = _email(email)
        if email_key:
            result[str(module_ref or "").strip()].add(email_key)
    return result


def _module_expected_emails(session, assigned_by_module, identity_emails=None) -> set[str]:
    expected = set(assigned_by_module.get(str(session.module_catalogue_id or "").strip(), set()))
    if identity_emails is not None:
        expected &= set(identity_emails)
    return expected


def _local_datetime(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def _graph_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value or "").strip())
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _attendance_interval_bounds(intervals) -> tuple[datetime | None, datetime | None]:
    first_join = None
    last_leave = None
    for interval in intervals if isinstance(intervals, list) else []:
        if not isinstance(interval, dict):
            continue
        joined = _graph_datetime(interval.get("joinDateTime"))
        left = _graph_datetime(interval.get("leaveDateTime"))
        if joined is not None and (first_join is None or joined < first_join):
            first_join = joined
        if left is not None and (last_leave is None or left > last_leave):
            last_leave = left
    return first_join, last_leave


def fetch_verified_teams_attendance_rows(
    learner_ids: list[int] | None = None,
    learner_emails: list[str] | None = None,
    module_refs: list[str] | None = None,
    *,
    all_learners: bool = False,
    start_date=None,
    end_date=None,
    include_reported_participants: bool = False,
    occurrence_ids: list[str] | None = None,
    independent_learner_scope: bool = False,
    verified_profile=None,
    include_group_metadata: bool = True,
    register_schedule=None,
    parallel_delivery_guests=None,
) -> list[dict]:
    """Build real attendance from completed Microsoft Teams reports.

    Expected learners come from the module's learner training plans. Microsoft
    organizers, presenters and unrelated guests are never learner attendance
    rows merely because they were invited or joined the meeting. Approved
    alternative-session recovery guests extend only their selected occurrence.
    """

    # Retained for API compatibility with coach callers. Report participants
    # are now always narrowed to the authoritative module learner roster.
    del include_reported_participants, independent_learner_scope

    ids = sorted({int(value) for value in (learner_ids or []) if value})
    emails = sorted({_email(value) for value in (learner_emails or []) if _email(value)})
    modules = sorted({str(value).strip() for value in (module_refs or []) if str(value).strip()})
    if not ids and not emails and not modules and not all_learners:
        return []

    database = router.db_for_read(LearnerProfile) or "default"

    # A caller-supplied learner scope narrows which invitees count, resolved
    # up front to normalised emails (and kept, so the later learner lookup
    # need not repeat this same query). `None` means "no narrowing": every
    # invitee of an in-scope session counts, as opposed to an empty dict,
    # which would wrongly exclude everyone.
    identity_learners_by_email: dict[str, LearnerProfile] | None = None
    if ids or emails:
        identity_filter = Q()
        if ids:
            identity_filter |= Q(id__in=ids)
        if emails:
            identity_filter |= Q(email_normalized__in=emails)
        # Only reuse a profile already verified by the catalogue's owner check.
        # Keep the full-roster read below: name ambiguity cannot be checked on
        # this one learner alone.
        if (verified_profile is not None and not ids and len(emails) == 1
                and _email(verified_profile.email) == emails[0]):
            identity_learners_by_email = {emails[0]: verified_profile}
        else:
            with projection_operation('attendance_profiles'):
                identity_learners_by_email = {
                    _email(learner.email): learner
                    for learner in LearnerProfile.objects.using(database).filter(identity_filter).only(
                        'id', 'enrolment_id', 'full_name', 'email', 'coach_name',
                    )
                    if _email(learner.email)
                }
        if not identity_learners_by_email:
            return []

    from .alternative_recovery import approved_alternative_guests
    # Include all approved guests in the ambiguity check; narrow returned
    # attendance below, after deciding whether a full name is unique.
    approved_guests = approved_alternative_guests(database, None,
        **({'occurrence_ids': occurrence_ids} if occurrence_ids is not None else {}))
    # Internal read-time projection only: callers resolve equivalent curriculum
    # slots first. This does not enrol, invite, or persist guests in the series.
    parallel_delivery_guests = parallel_delivery_guests or {}
    approved_guests = {key: set(value) for key, value in approved_guests.items()}
    for key, guests in parallel_delivery_guests.items():
        approved_guests.setdefault(key, set()).update(guests)
    if register_schedule is not None and identity_learners_by_email is not None:
        # Register projection discards other occurrences, except independently
        # approved recovery guests. Keep all those alternatives and their full
        # ambiguity roster; a schedule is scope, never attendance evidence.
        relevant = {str(row.get('attendance_source_occurrence_id') or row['session_id'])
                    if row.get('attendance_source_module_id') else str(row['session_id'])
                    for row in register_schedule}
        relevant.update(key for key, guests in approved_guests.items()
                        if set(guests) & identity_learners_by_email.keys())
        occurrence_ids = sorted(relevant)
    session_queryset = LiveSession.objects.using(database)
    if identity_learners_by_email is not None:
        # Series membership is the same normalized plan roster used below.
        # Retain approved alternatives outside that roster and still load the
        # entire selected module roster before resolving ambiguous identities.
        assigned_refs = LearnerTrainingPlanModule.objects.using(database).filter(
            learner__email_normalized__in=list(identity_learners_by_email),
        ).values('module_ref')
        guest_ids = [key for key, guests in approved_guests.items()
                     if set(guests) & identity_learners_by_email.keys()]
        session_queryset = session_queryset.filter(
            Q(module_catalogue_id__in=assigned_refs)
            | Q(id__in=LiveSessionOccurrence.objects.using(database)
                .filter(id__in=guest_ids).values('live_session_id')))
    if occurrence_ids is not None:
        if not occurrence_ids:
            return []
        session_queryset = session_queryset.filter(id__in=LiveSessionOccurrence.objects.using(database)
            .filter(id__in=occurrence_ids).values('live_session_id'))
    if modules:
        session_queryset = session_queryset.filter(module_catalogue_id__in=modules)
    sessions = list(
        session_queryset.only("id", "module_catalogue_id", "module_title")
    )
    if not sessions:
        return []

    assigned_by_module = projection_call('attendance_roster', _assigned_learner_emails_by_module,
        database, [session.module_catalogue_id for session in sessions],
    )
    expected_emails_by_session: dict[str, set[str]] = {}
    all_expected_emails: set[str] = set()
    for session in sessions:
        session_emails = _module_expected_emails(
            session,
            assigned_by_module,
            identity_learners_by_email.keys() if identity_learners_by_email is not None else None,
        )
        if not session_emails:
            continue
        expected_emails_by_session[session.id] = session_emails
        all_expected_emails.update(session_emails)

    # Approved recovery guests extend only the selected occurrence.  They do
    # not become module/group members and are never added to the recurring
    # Teams series attendee snapshot.
    occurrence_series = {}
    if approved_guests:
        occurrence_series = dict(
            LiveSessionOccurrence.objects.using(database)
            .filter(
                pk__in=list(approved_guests),
                live_session_id__in=[session.id for session in sessions],
            )
            .values_list("id", "live_session_id")
        )
        for occurrence_id, guest_emails in approved_guests.items():
            session_id = occurrence_series.get(occurrence_id)
            if not session_id:
                continue
            scoped_guests = set(guest_emails)
            if identity_learners_by_email is not None:
                scoped_guests &= identity_learners_by_email.keys()
            if not scoped_guests:
                continue
            expected_emails_by_session.setdefault(session_id, set()).update(scoped_guests)
            all_expected_emails.update(scoped_guests)
    if not all_expected_emails:
        return []

    sessions_by_id = {
        session.id: session for session in sessions if session.id in expected_emails_by_session
    }

    # Check name uniqueness against the entire module roster before narrowing
    # the output to the requested learner(s). A one-learner query must not turn
    # two identical names into a unique match or credit both coach profiles.
    matching_emails = set(all_expected_emails)
    for session in sessions_by_id.values():
        matching_emails.update(assigned_by_module.get(str(session.module_catalogue_id or '').strip(), set()))
    for occurrence_id, session_id in occurrence_series.items():
        if session_id in sessions_by_id:
            matching_emails.update(approved_guests.get(occurrence_id, set()))
    with projection_operation('attendance_profiles'):
        matching_learners = {
            _email(learner.email): learner
            for learner in LearnerProfile.objects.using(database).filter(
                email_normalized__in=matching_emails,
            ).only('id', 'enrolment_id', 'full_name', 'email', 'coach_name')
        }
    learners_by_email = {email: learner for email, learner in matching_learners.items() if email in all_expected_emails}
    if not learners_by_email:
        return []

    module_refs_needed = {
        str(session.module_catalogue_id or "").strip() for session in sessions_by_id.values()
    }
    module_rows = ModuleAuthoringModule.objects.using(database).filter(
        module_catalogue_id__in=module_refs_needed,
    ).only("module_catalogue_id", "group_id", "group_name")
    modules_by_id = {row.module_catalogue_id: row for row in module_rows} if include_group_metadata else {}

    occurrence_queryset = (
        LiveSessionOccurrence.objects.using(database)
        .filter(live_session_id__in=list(sessions_by_id), actual_end__isnull=False)
    )
    if occurrence_ids is not None:
        occurrence_queryset = occurrence_queryset.filter(id__in=occurrence_ids)
    # Monthly/reporting callers only need a bounded slice. Applying the range
    # before loading reports prevents one request from materialising every
    # historical occurrence and attendance record for the whole caseload.
    if start_date is not None:
        occurrence_queryset = occurrence_queryset.filter(
            Q(actual_start__date__gte=start_date)
            | Q(actual_start__isnull=True, scheduled_start__date__gte=start_date)
        )
    if end_date is not None:
        occurrence_queryset = occurrence_queryset.filter(
            Q(actual_start__date__lte=end_date)
            | Q(actual_start__isnull=True, scheduled_start__date__lte=end_date)
        )
    occurrences = projection_call('teams_occurrences', list,
        occurrence_queryset.only('id', 'live_session_id', 'actual_start', 'actual_end',
            'scheduled_start', 'scheduled_end', 'session_number', 'attendance_report_id',
            'artifacts_synced_at', 'updated_at').order_by("scheduled_start", "session_number", "id")
    )
    if not occurrences:
        return []

    # The native schedule already read this same replacement lateral join.
    # Reuse concrete destination associations only; shared aliases and recovery
    # alternatives without those facts still use the authoritative SQL reader.
    known_sources = {str(row['session_id']): {
        'occurrence_id': str(row.get('attendance_source_occurrence_id') or row['session_id']),
        'attendance_report_id': row.get('attendance_report_id') or '',
    } for row in (register_schedule or []) if not row.get('attendance_source_module_id')
      and 'attendance_source_occurrence_id' in row and 'attendance_report_id' in row}
    missing_sources = [occurrence.id for occurrence in occurrences if str(occurrence.id) not in known_sources]
    replaced_sources = {**known_sources, **(projection_call('attendance_associations', _replaced_attendance_sources, database, missing_sources)
                                          if missing_sources else {})}
    attendance_source_by_occurrence = {
        str(occurrence.id): str(
            replaced_sources.get(str(occurrence.id), {}).get('occurrence_id')
            or occurrence.id
        )
        for occurrence in occurrences
    }
    attendance_report_by_occurrence = {
        str(occurrence.id): str((
            replaced_sources[str(occurrence.id)].get('attendance_report_id')
            if (str(occurrence.id) in replaced_sources
                and str(replaced_sources[str(occurrence.id)].get('occurrence_id')) != str(occurrence.id))
            else occurrence.attendance_report_id
        ) or '').strip()
        for occurrence in occurrences
    }
    occurrences = [
        occurrence for occurrence in occurrences
        if attendance_report_by_occurrence[str(occurrence.id)]
    ]
    if not occurrences:
        return []

    occurrences_by_id = {occurrence.id: occurrence for occurrence in occurrences}
    expected_emails_by_occurrence = {}
    for occurrence in occurrences:
        session = sessions_by_id.get(occurrence.live_session_id)
        if session is None:
            continue
        expected_emails_by_occurrence[occurrence.id] = (
            _module_expected_emails(
                session,
                assigned_by_module,
                identity_learners_by_email.keys() if identity_learners_by_email is not None else None,
            )
            | set(approved_guests.get(occurrence.id, set()))
        )
    source_occurrence_ids = {
        attendance_source_by_occurrence[str(occurrence.id)] for occurrence in occurrences
    }
    if register_schedule is not None:
        alias_rows, link_rows = projection_call('reviewed_links', _attendance_review_rows, database, assigned_by_module, source_occurrence_ids)
    else:
        alias_rows = _attendance_alias_rows(database, assigned_by_module)
        link_rows = _attendance_identity_link_rows(database, source_occurrence_ids)
    aliases_by_module = defaultdict(dict)
    for module_ref, alias_email, canonical_email in alias_rows:
        alias_key, canonical_key = _email(alias_email), _email(canonical_email)
        if alias_key and canonical_key:
            aliases_by_module[str(module_ref or '').strip()][alias_key] = canonical_key
    reviewed_identity_links = {
        (str(occurrence_id), str(attendance_row_id)): _email(canonical_email)
        for occurrence_id, attendance_row_id, canonical_email
        in link_rows
        if _email(canonical_email)
    }

    attendance_by_occurrence: dict[str, dict[str, dict]] = defaultdict(dict)
    target_occurrences_by_source: dict[str, list[str]] = defaultdict(list)
    for occurrence in occurrences:
        target_occurrences_by_source[
            attendance_source_by_occurrence[str(occurrence.id)]
        ].append(occurrence.id)
    attendance_records = projection_call('teams_evidence', list,
        LiveSessionAttendance.objects.using(database)
        .filter(occurrence_id__in=source_occurrence_ids)
        .only(
            "id",
            "occurrence_id",
            "graph_record_id",
            "email",
            "display_name",
            "role",
            "raw_data",
            "total_attendance_seconds",
            "intervals",
        )
    )
    rules_by_module = defaultdict(list)
    for rule in projection_call('identity_rules', identity_rules, list(assigned_by_module), database):
        rules_by_module[rule['module_catalogue_id']].append(rule)
    raw_by_source = defaultdict(list)
    for record in attendance_records:
        raw_by_source[str(record.occurrence_id)].append({
            'id': str(record.id), 'occurrence_id': str(record.occurrence_id),
            'email': record.email, 'display_name': record.display_name,
            'raw_data': record.raw_data, 'role': record.role,
        })
    resolved_by_context = {}
    identity_rosters = {}
    module_alias_rows = {module_ref:[{'alias_email':key, 'canonical_email':value} for key,value in aliases.items()]
                         for module_ref,aliases in aliases_by_module.items()}
    legacy_links = [{'occurrence_id': occurrence, 'attendance_row_id': source,
                     'canonical_email': email} for (occurrence, source), email in reviewed_identity_links.items()]
    for record in attendance_records:
        source_row_id = str(record.id or '').strip()
        for target_occurrence_id in target_occurrences_by_source.get(
            str(record.occurrence_id), []
        ):
            occurrence = occurrences_by_id.get(target_occurrence_id)
            session = sessions_by_id.get(occurrence.live_session_id) if occurrence else None
            module_ref = str(session.module_catalogue_id or '').strip() if session else ''
            context_key = (module_ref, str(target_occurrence_id))
            if context_key not in resolved_by_context:
                roster_key = (module_ref, frozenset(approved_guests.get(target_occurrence_id, set())))
                if roster_key not in identity_rosters:
                    eligible = assigned_by_module.get(module_ref, set()) | set(roster_key[1])
                    identity_rosters[roster_key] = {email: {'name':learner.full_name, 'learnerProfileId':learner.id}
                        for email,learner in matching_learners.items() if email in eligible}
                resolved_by_context[context_key] = {str(row['id']): row for row in projection_call('identity_matching', apply_reviewed_identities,
                    raw_by_source[str(record.occurrence_id)], module_alias_rows.get(module_ref, []), legacy_links,
                    rules_by_module.get(module_ref, []), learners=identity_rosters[roster_key])}
            resolved = resolved_by_context[context_key][source_row_id]
            blocked_identity = resolved.get('identityReviewRequired')
            email_key = '' if blocked_identity else _email(resolved.get('email'))
            email_keys = {email_key} if email_key else set()
            for email_key in email_keys & expected_emails_by_occurrence.get(target_occurrence_id, set()):
                first_join, last_leave = _attendance_interval_bounds(record.intervals)
                existing = attendance_by_occurrence[target_occurrence_id].get(email_key)
                if existing is None:
                    attendance_by_occurrence[target_occurrence_id][email_key] = {
                        "total_attendance_seconds": max(record.total_attendance_seconds or 0, 0),
                        "records": [{"intervals": record.intervals, "total_attendance_seconds": record.total_attendance_seconds}],
                        "source_record_id": str(record.graph_record_id or record.id or "").strip(),
                        "first_join_at": first_join,
                        "last_leave_at": last_leave,
                    }
                else:
                    existing["records"].append({"intervals": record.intervals, "total_attendance_seconds": record.total_attendance_seconds})
                    if not existing["source_record_id"] and record.graph_record_id:
                        existing["source_record_id"] = str(record.graph_record_id).strip()
                    if first_join is not None and (
                        existing["first_join_at"] is None or first_join < existing["first_join_at"]
                    ):
                        existing["first_join_at"] = first_join
                    if last_leave is not None and (
                        existing["last_leave_at"] is None or last_leave > existing["last_leave_at"]
                    ):
                        existing["last_leave_at"] = last_leave

    for people in attendance_by_occurrence.values():
        for evidence in people.values():
            evidence["total_attendance_seconds"] = evidence_seconds(evidence.pop("records"))
    rows = []
    calculated_at = timezone.now()
    for occurrence in occurrences:
        session = sessions_by_id.get(occurrence.live_session_id)
        if session is None:
            continue
        module_ref = str(session.module_catalogue_id or "").strip()
        start = _local_datetime(occurrence.actual_start or occurrence.scheduled_start)
        end = _local_datetime(occurrence.actual_end or occurrence.scheduled_end)
        if start is None:
            continue
        title = str(session.module_title or "").strip() or "Live session"
        if occurrence.session_number:
            title = f"{title} — Session {occurrence.session_number}"

        # group_id/group_name are display metadata only now -- a module row
        # that can't be found no longer hides an otherwise-valid invite match.
        module = modules_by_id.get(module_ref)

        occurrence_expected = expected_emails_by_occurrence.get(occurrence.id, set())
        for email in occurrence_expected & expected_emails_by_session.get(session.id, set()):
            learner = learners_by_email.get(email)
            if learner is None:
                continue
            attendance = attendance_by_occurrence[occurrence.id].get(email)
            rows.append(
                {
                    "learner_id": learner.id,
                    "learner_profile_id": learner.id,
                    # The engagement app's learner id space is Created_users.id
                    # (enrolment_id), not this row's own learner_id/learner_profile_id
                    # (LearnerProfile.id). Carried through Python-side only, for the
                    # engagement award step below; not written to the reporting table.
                    "enrolment_id": learner.enrolment_id,
                    "learner_name": learner.full_name,
                    "learner_email": learner.email,
                    "session_id": occurrence.id,
                    "occurrence_id": occurrence.id,
                    "live_session_id": session.id,
                    "session_title": title,
                    "session_type": "live_session",
                    "session_date": start.date(),
                    "session_start_time": start.time().replace(tzinfo=None),
                    "session_end_time": end.time().replace(tzinfo=None) if end else None,
                    "attendance_status": "present" if attendance and attendance["total_attendance_seconds"] > 180 else "absent",
                    "minutes_late": 0,
                    "catchup_completed": False,
                    "absence_reason": "",
                    "attended_seconds": attendance["total_attendance_seconds"] if attendance else 0,
                    "attendance_report_id": attendance_report_by_occurrence[str(occurrence.id)],
                    "source_record_id": attendance["source_record_id"] if attendance else "",
                    "module_catalogue_id": module_ref,
                    "module_title": str(session.module_title or "").strip(),
                    "group_id": str(module.group_id or "").strip() if module else "",
                    "group_name": str(module.group_name or "").strip() if module else "",
                    "is_expected": email in occurrence_expected,
                    "eligibility_reason": (
                        "parallel_delivery_guest" if email in parallel_delivery_guests.get(occurrence.id, set())
                        else "approved_recovery_guest" if email in approved_guests.get(occurrence.id, set())
                        else "module_assignment"
                    ),
                    "scheduled_start": occurrence.scheduled_start,
                    "scheduled_end": occurrence.scheduled_end,
                    "actual_start": occurrence.actual_start,
                    "actual_end": occurrence.actual_end,
                    "first_join_at": attendance["first_join_at"] if attendance else None,
                    "last_leave_at": attendance["last_leave_at"] if attendance else None,
                    "coach_name": learner.coach_name,
                    "calculated_at": calculated_at,
                    "updated_at": occurrence.artifacts_synced_at or occurrence.updated_at,
                }
            )

    from .session_recovery import apply_recovery_to_rows
    return projection_call('attendance_recovery', apply_recovery_to_rows, rows)


ATTENDANCE_REPORTING_TABLE = '"Learner"."learner_attendance_details"'
_ATTENDANCE_SCHEMA_READY_DATABASES: set[str] = set()


def ensure_teams_attendance_reporting_columns(database: str = "default") -> None:
    if database in _ATTENDANCE_SCHEMA_READY_DATABASES:
        return
    # PostgreSQL DDL is transactional. Keep the additive schema upgrade, its
    # backfill and the view replacement all-or-nothing on Neon.
    with transaction.atomic(using=database), connections[database].cursor() as cursor:
        # This legacy trigger targets the removed Learner.Absence table and
        # prevents every insert/update on the reporting table.
        cursor.execute(
            f"""
            DROP TRIGGER IF EXISTS learner_attendance_details_sync_absence_counts
            ON {ATTENDANCE_REPORTING_TABLE}
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE {ATTENDANCE_REPORTING_TABLE}
                ADD COLUMN IF NOT EXISTS attended_seconds integer NOT NULL DEFAULT 0,
                ADD COLUMN IF NOT EXISTS attendance_report_id text,
                ADD COLUMN IF NOT EXISTS live_session_id text,
                ADD COLUMN IF NOT EXISTS source varchar(40) NOT NULL DEFAULT 'legacy',
                ADD COLUMN IF NOT EXISTS synced_at timestamp with time zone,
                ADD COLUMN IF NOT EXISTS enrolment_id bigint,
                ADD COLUMN IF NOT EXISTS learner_profile_id bigint,
                ADD COLUMN IF NOT EXISTS occurrence_id text,
                ADD COLUMN IF NOT EXISTS module_catalogue_id text,
                ADD COLUMN IF NOT EXISTS group_id text,
                ADD COLUMN IF NOT EXISTS group_name text,
                ADD COLUMN IF NOT EXISTS is_expected boolean NOT NULL DEFAULT true,
                ADD COLUMN IF NOT EXISTS eligibility_reason varchar(80),
                ADD COLUMN IF NOT EXISTS source_record_id text,
                ADD COLUMN IF NOT EXISTS scheduled_start timestamp with time zone,
                ADD COLUMN IF NOT EXISTS scheduled_end timestamp with time zone,
                ADD COLUMN IF NOT EXISTS actual_start timestamp with time zone,
                ADD COLUMN IF NOT EXISTS actual_end timestamp with time zone,
                ADD COLUMN IF NOT EXISTS first_join_at timestamp with time zone,
                ADD COLUMN IF NOT EXISTS last_leave_at timestamp with time zone,
                ADD COLUMN IF NOT EXISTS calculated_at timestamp with time zone
            """
        )
        cursor.execute(
            f"""
            UPDATE {ATTENDANCE_REPORTING_TABLE}
            SET learner_profile_id = COALESCE(learner_profile_id, learner_id),
                occurrence_id = COALESCE(NULLIF(occurrence_id, ''), session_id),
                calculated_at = COALESCE(calculated_at, synced_at, updated_at, created_at)
            WHERE source = 'microsoft_teams'
              AND (
                    learner_profile_id IS NULL
                 OR occurrence_id IS NULL
                 OR occurrence_id = ''
                 OR calculated_at IS NULL
              )
            """
        )
        cursor.execute(
            f"""
            UPDATE {ATTENDANCE_REPORTING_TABLE} attendance
            SET module_catalogue_id = session.module_catalogue_id,
                group_id = module.group_id,
                group_name = module.group_name
            FROM curriculum.live_sessions session
            LEFT JOIN curriculum.modules module
                   ON module.module_catalogue_id = session.module_catalogue_id
            WHERE attendance.source = 'microsoft_teams'
              AND attendance.live_session_id = session.id
              AND (
                    attendance.module_catalogue_id IS NULL
                 OR attendance.group_id IS NULL
                 OR attendance.group_name IS NULL
              )
            """
        )
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_attendance_details_source
            ON {ATTENDANCE_REPORTING_TABLE} (source)
            """
        )
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_attendance_details_learner_source_date
            ON {ATTENDANCE_REPORTING_TABLE} (learner_id, source, session_date DESC)
            """
        )
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_attendance_details_module_group
            ON {ATTENDANCE_REPORTING_TABLE} (module_catalogue_id, group_id)
            WHERE source = 'microsoft_teams' AND is_expected
            """
        )
        cursor.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_details_profile_occurrence
            ON {ATTENDANCE_REPORTING_TABLE} (learner_profile_id, occurrence_id)
            WHERE source = 'microsoft_teams'
              AND learner_profile_id IS NOT NULL
              AND occurrence_id IS NOT NULL
            """
        )
        cursor.execute(
            f"""
            CREATE OR REPLACE VIEW "Learner"."verified_teams_attendance" AS
            SELECT *
            FROM {ATTENDANCE_REPORTING_TABLE}
            WHERE source = 'microsoft_teams' AND is_expected
            """
        )
    transaction.on_commit(
        lambda: _ATTENDANCE_SCHEMA_READY_DATABASES.add(database),
        using=database,
    )


def _verified_occurrence_ids(database: str, module_refs: list[str] | None = None) -> list[str]:
    sessions = LiveSession.objects.using(database).all()
    modules = sorted({str(value).strip() for value in (module_refs or []) if str(value).strip()})
    if modules:
        sessions = sessions.filter(module_catalogue_id__in=modules)
    session_ids = list(sessions.values_list("id", flat=True))
    if not session_ids:
        return []
    return list(
        LiveSessionOccurrence.objects.using(database)
        .filter(live_session_id__in=session_ids)
        .exclude(Q(attendance_report_id__isnull=True) | Q(attendance_report_id__exact=""))
        .values_list("id", flat=True)
    )


def sync_verified_teams_attendance_reporting(
    learner_ids: list[int] | None = None,
    learner_emails: list[str] | None = None,
    module_refs: list[str] | None = None,
    *,
    all_learners: bool = False,
) -> int:
    """Rebuild the scoped part of the learner attendance read model."""

    database = router.db_for_write(LearnerProfile) or "default"
    scoped_modules = sorted(
        {str(value).strip() for value in (module_refs or []) if str(value).strip()}
    )
    rows = fetch_verified_teams_attendance_rows(
        learner_ids,
        learner_emails,
        module_refs,
        all_learners=all_learners,
    )
    from curriculum_api.shared_session_register import shared_register_rows
    shared_rows = shared_register_rows(database=database, module_refs=module_refs,
        learner_ids=learner_ids, learner_emails=learner_emails, all_learners=all_learners)
    occurrence_ids = (
        _verified_occurrence_ids(database, module_refs)
        if all_learners or module_refs
        else []
    )
    if not rows and not shared_rows and not occurrence_ids and not scoped_modules and not all_learners:
        return 0

    query = f"""
        INSERT INTO {ATTENDANCE_REPORTING_TABLE} (
            learner_id, learner_name, learner_email,
            learner_profile_id, enrolment_id,
            session_id, session_title, session_type,
            occurrence_id,
            session_date, session_start_time, session_end_time,
            attendance_status, minutes_late, absence_reason,
            catchup_completed, coach_name, module_title,
            attended_seconds, attendance_report_id, live_session_id,
            module_catalogue_id, group_id, group_name,
            is_expected, eligibility_reason, source_record_id,
            scheduled_start, scheduled_end, actual_start, actual_end,
            first_join_at, last_leave_at,
            source, synced_at, calculated_at, updated_at
        ) VALUES (
            %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            'microsoft_teams', CURRENT_TIMESTAMP, %s, %s
        )
        ON CONFLICT (learner_id, session_id) DO UPDATE SET
            learner_name = EXCLUDED.learner_name,
            learner_email = EXCLUDED.learner_email,
            learner_profile_id = EXCLUDED.learner_profile_id,
            enrolment_id = EXCLUDED.enrolment_id,
            session_title = EXCLUDED.session_title,
            session_type = EXCLUDED.session_type,
            occurrence_id = EXCLUDED.occurrence_id,
            session_date = EXCLUDED.session_date,
            session_start_time = EXCLUDED.session_start_time,
            session_end_time = EXCLUDED.session_end_time,
            attendance_status = EXCLUDED.attendance_status,
            minutes_late = EXCLUDED.minutes_late,
            absence_reason = EXCLUDED.absence_reason,
            catchup_completed = EXCLUDED.catchup_completed,
            coach_name = EXCLUDED.coach_name,
            module_title = EXCLUDED.module_title,
            attended_seconds = EXCLUDED.attended_seconds,
            attendance_report_id = EXCLUDED.attendance_report_id,
            live_session_id = EXCLUDED.live_session_id,
            module_catalogue_id = EXCLUDED.module_catalogue_id,
            group_id = EXCLUDED.group_id,
            group_name = EXCLUDED.group_name,
            is_expected = EXCLUDED.is_expected,
            eligibility_reason = EXCLUDED.eligibility_reason,
            source_record_id = EXCLUDED.source_record_id,
            scheduled_start = EXCLUDED.scheduled_start,
            scheduled_end = EXCLUDED.scheduled_end,
            actual_start = EXCLUDED.actual_start,
            actual_end = EXCLUDED.actual_end,
            first_join_at = EXCLUDED.first_join_at,
            last_leave_at = EXCLUDED.last_leave_at,
            source = EXCLUDED.source,
            synced_at = CURRENT_TIMESTAMP,
            calculated_at = EXCLUDED.calculated_at,
            updated_at = EXCLUDED.updated_at
    """
    params = [
        (
            row["learner_id"],
            row["learner_name"],
            row["learner_email"],
            row["learner_profile_id"],
            row["enrolment_id"],
            row["session_id"],
            row["session_title"],
            row["session_type"],
            row["occurrence_id"],
            row["session_date"],
            row["session_start_time"],
            row["session_end_time"],
            row["attendance_status"],
            row["minutes_late"],
            row["absence_reason"],
            row["catchup_completed"],
            row["coach_name"],
            row["module_title"],
            row["attended_seconds"],
            row["attendance_report_id"],
            row["live_session_id"],
            row["module_catalogue_id"],
            row["group_id"],
            row["group_name"],
            row["is_expected"],
            row["eligibility_reason"],
            row["source_record_id"],
            row["scheduled_start"],
            row["scheduled_end"],
            row["actual_start"],
            row["actual_end"],
            row["first_join_at"],
            row["last_leave_at"],
            row["calculated_at"],
            row["updated_at"],
        )
        for row in rows
    ]
    with transaction.atomic(using=database):
        # Reconcile eligibility and publish the register together. Schema changes
        # belong to the owner-run SQL, never to a background synchronization.
        with connections[database].cursor() as check:
            check.execute(f"SELECT attended_seconds, occurrence_id, is_expected, catchup_completed FROM {ATTENDANCE_REPORTING_TABLE} LIMIT 0")
        with connections[database].cursor() as cursor:
            # Retain stale rows for audit/absence-report foreign keys, but hide
            # them from the read view until this module-roster rebuild marks
            # them expected again in the upsert below.
            if all_learners:
                cursor.execute(
                    f"""
                    UPDATE {ATTENDANCE_REPORTING_TABLE}
                    SET is_expected = false,
                        eligibility_reason = 'not_in_current_module_roster',
                        calculated_at = CURRENT_TIMESTAMP
                    WHERE source = 'microsoft_teams'
                    """
                )
            elif scoped_modules:
                cursor.execute(
                    f"""
                    UPDATE {ATTENDANCE_REPORTING_TABLE}
                    SET is_expected = false,
                        eligibility_reason = 'not_in_current_module_roster',
                        calculated_at = CURRENT_TIMESTAMP
                    WHERE source = 'microsoft_teams'
                      AND (
                            module_catalogue_id = ANY(%s)
                         OR occurrence_id = ANY(%s)
                      )
                    """,
                    [scoped_modules, occurrence_ids],
                )
            if params:
                cursor.executemany(query, params)
            upsert_live_session_register(cursor, [*rows, *shared_rows])
    _award_attendance_points(rows)
    return len(rows) + len(shared_rows)


def _award_attendance_points(rows):
    """Grant `live_session_attended` for each verified 'present' row just synced.

    Best-effort and safe to call on every sync, including re-syncs of rows
    already upserted above: grant_points() de-dupes on
    (rule, event_reference), so a session already awarded is a no-op here.
    Skips any learner with no enrolment_id (their Created_users row has been
    removed) — there is no engagement id to attribute the grant to.
    """
    try:
        from engagement_api.services import grant_points
    except Exception:  # noqa: BLE001 — engagement points must never break an attendance sync
        return

    for row in rows:
        if row.get("attendance_status") != "present":
            continue
        enrolment_id = row.get("enrolment_id")
        if enrolment_id is None:
            continue
        try:
            grant_points(
                "live_session_attended", str(enrolment_id), row.get("learner_name") or "",
                event_reference=f"attendance:{row['session_id']}:learner:{enrolment_id}",
                source_type="attendance", source_id=str(row["session_id"]),
            )
        except Exception:  # noqa: BLE001 — a dropped grant must never break the sync
            continue
