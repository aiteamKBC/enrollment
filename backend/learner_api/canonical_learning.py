"""Shared reads of the consolidated learning record.

Source snapshots are lineage, not additional hours. Reads never sync, run AI,
repair data, or create records. Route identities are verified before reading.
"""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
import re

from old_otjh import repository
from old_otjh.repository import query
from old_otjh.service import ServiceError
from .ksb_exclusions import not_excluded_sql
from .monthly_log_sources import decoded, number, row
from .current_learning import (
    current_records, current_records_bulk, is_unsynchronised_direct_save, source_payload_metadata,
)

UK = ZoneInfo('Europe/London')


def _ksb_not_excluded(alias='k'):
    """Skip KSB rows the standards migration registered as not counting."""
    return not_excluded_sql(alias, repository._query_alias.get() or repository.DB)


def is_legacy_record(record):
    """Return the row's lineage classification, never its counting policy."""
    return str(record.get('source_system') or '').strip().casefold() == 'old_lms'


def counts_as_actual(record):
    """Count accepted progress rows, independently of their source lineage."""
    return record.get('accepted') is True


def is_time_estimated(record):
    """Return whether a reporting allocation is provisional and needs approval."""
    if record.get('reporting_estimated') is True or record.get('actual_estimated') is True:
        return True
    payload = source_payload_metadata(record.get('source_payload'))
    reconciliation = payload.get('reconciliation')
    allocation = reconciliation.get('reporting_allocation') if isinstance(reconciliation, dict) else None
    return isinstance(allocation, dict) and allocation.get('estimated') is True


def counts_as_completed(record, include_source_evidence=True):
    """Completion and accepted OTJ hours are deliberately separate facts.

    Source completion is trusted for the old LMS lineage. Callers that are
    projecting a non-journal/coach view can disable source evidence while still
    retaining explicit completion and status-based completion.
    """
    if record.get('completed') is True or record.get('accepted') is True:
        return True
    statuses = {str(record.get('activity_status') or '').strip().casefold()}
    statuses.update(
        str(source.get('activity_status') or '').strip().casefold()
        for source in (record.get('sources') or []) if isinstance(source, dict)
    )
    if include_source_evidence and any(
            source.get('completed') is True
            and str(source.get('source_system') or '').strip().casefold() in {'', 'old_lms'}
            for source in (record.get('sources') or []) if isinstance(source, dict)):
        return True
    return bool(statuses & {
        'accepted', 'complete', 'completed', 'passed',
    })


def enabled(learner_id):
    return profile(learner_id) is not None


def require_profile(learner_id):
    """Resolve the SSOT owner or fail closed; learner pages have no legacy fallback."""
    owner = profile(learner_id)
    if owner is None:
        raise ServiceError(
            'The learner is not linked to the consolidated learning record.',
            'ssot_identity_required',
            409,
        )
    return owner


def profile(learner_id):
    records = query('''SELECT l.id,l.enrolment_id,l.aptem_id,l.programme_id,l.full_name AS name,
        l.programme,l.coach_name,l.coach_email,l.email,l.start_date,l.end_date,l.learner_type,
        u.id AS account_record_id,u."Email" AS account_email,u.aptem_id AS account_aptem_id,
        u."Planned_hours" AS programme_planned_hours,
        EXISTS(SELECT 1 FROM "Learner".progress_transfer_requests t WHERE t.enrolment_id=l.enrolment_id) AS progress_transfer_active
        FROM "Learner".learners l
        LEFT JOIN enrolment."Created_users" u ON u.id=l.enrolment_id
        WHERE l.enrolment_id=%s''', [learner_id])
    if not records:
        return None
    if len(records) != 1:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    return _validated_profile(records[0])


def _validated_profile(owner):
    email = str(owner.get('email') or '').strip().casefold()
    account_email = str(owner.get('account_email') or '').strip().casefold()
    aptem = str(owner.get('account_aptem_id') or '').strip().lstrip('0')
    if (owner.get('account_record_id') is None or not email or email != account_email
            or (aptem and aptem != str(owner.get('aptem_id') or '').lstrip('0'))):
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    return owner


def programme_planned_hours(learner_id, *, owner=None):
    """The enrolment's overall plan is independent of the scheduled month totals."""
    from math import isfinite

    owner = profile(learner_id) if owner is None else owner
    value = owner.get('programme_planned_hours') if owner else None
    try:
        hours = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(hours, 4) if isfinite(hours) and hours >= 0 else None


def profile_by_aptem(aptem_id):
    """For already-authorized Audit callers; never use an Aptem ID as a PK."""
    records = query('''SELECT id,enrolment_id,aptem_id,programme_id,full_name AS name,
        programme,coach_name,coach_email,email,start_date,end_date,learner_type
        FROM "Learner".learners WHERE aptem_id=%s''', [aptem_id])
    if len(records) != 1:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    return records[0]


def entries(learner_id):
    owner = profile(learner_id)
    if owner is None:
        return []
    return entries_for(owner)


def entries_for(owner, *, overview_only=False, entry_ids=None, lineage_only=False, dashboard_metadata=False, training_summary=False, monthly_summary=False, catalogue_summary=False):
    """``lineage_only`` keeps every progress column but replaces an object
    ``source_payload`` (raw evidence) with just its ``original_source_ref``,
    the only key the completion and hours projection reads.

    ``dashboard_metadata`` also keeps the minimal course placement and
    provisional-allocation metadata required by Dashboard display rules.

    ``training_summary`` retains completion, timing and lineage facts without
    evidence bodies or reporting-segment/historical-component payloads. The
    complete learner fact set preserves cross-module native-save deduplication.

    ``monthly_summary`` uses explicit progress facts instead of progress.*,
    retaining full reporting segments, source links and historical attribution.
    It never restricts the learner-wide reconciliation population by month.
    """
    from learner_api.projection_performance import projection_stage

    # My Learning needs lineage/completion and canonical seconds, never KSB
    # metrics or reporting-history bodies. Presence of segments still gates
    # unsynchronised saves; historical source identities still match attempts.
    if catalogue_summary:
        training_summary = True
    # Optional caller-owned selection is applied before the evidence/lineage
    # joins. The learner predicate always remains mandatory.
    selection = '' if entry_ids is None else ' AND progress.id=ANY(%s)'
    params = [owner['id']] if entry_ids is None else [owner['id'], list(entry_ids)]
    columns = 'progress.*'
    payload = 'to_jsonb(p)'
    if lineage_only:
        payload = '''CASE WHEN jsonb_typeof(p.source_payload)='object'
            THEN to_jsonb(p)-'source_payload'||jsonb_build_object('source_payload',
                 jsonb_build_object('retakeTimeChoice',p.source_payload->'retakeTimeChoice','original_source_ref',p.source_payload->'original_source_ref'))
            ELSE to_jsonb(p) END'''
    if dashboard_metadata or (training_summary and not catalogue_summary):
        # Dashboard summaries need the course's introduction marker and the
        # provisional-allocation verdict. Extract those object keys in SQL.
        # Preserve encoded-string/nonobject payloads as legacy lineage does;
        # source_payload_metadata supports encoded JSON without unsafe SQL casts.
        payload = '''CASE WHEN jsonb_typeof(p.source_payload)='object'
            THEN to_jsonb(p)-'source_payload'||jsonb_build_object('source_payload',
            jsonb_build_object('retakeTimeChoice',p.source_payload->'retakeTimeChoice','original_source_ref',p.source_payload->'original_source_ref',
                'course_display_placement',jsonb_build_object(
                    'course_id',p.source_payload->'course_display_placement'->'course_id',
                    'section',p.source_payload->'course_display_placement'->'section'),
                'reconciliation',jsonb_build_object('reporting_allocation',jsonb_build_object(
                    'estimated',p.source_payload->'reconciliation'->'reporting_allocation'->'estimated'))))
            ELSE to_jsonb(p) END'''
    if overview_only or training_summary or monthly_summary:
        # Completion projection needs saved timing/attempt facts, never raw
        # evidence bodies. Keep lineage required by recorded course allocation.
        columns = ','.join('progress.' + field for field in (
            'id', 'learner_id', 'kind', 'module_ref', 'module_title', 'component_ref',
            'component_title', 'component_type', 'quiz_ref', 'passed', 'reported_time',
            'submitted_at', 'declared_completed_at', 'started_at', 'time_tracking_source', 'claimed_seconds',
            'verified_seconds', 'expected_otjh', 'source_system', 'component_link_source',
            'actual_seconds', 'accepted', 'reporting_started_at', 'reporting_ended_at',
            'reporting_month', 'achieved_score', 'total_score', 'activity_status'))
        if catalogue_summary:
            # Narrow evidence before materialising/serialising the canonical
            # CTE row, rather than putting its full body in to_jsonb(p) first.
            columns += """,CASE WHEN jsonb_typeof(progress.source_payload)='object'
                THEN jsonb_build_object('retakeTimeChoice',progress.source_payload->'retakeTimeChoice','original_source_ref',progress.source_payload->'original_source_ref')
                ELSE progress.source_payload END AS source_payload,
                progress.feed_kind,to_jsonb(progress)->'actual_basis' AS actual_basis"""
            payload = 'to_jsonb(p)'
        elif training_summary or monthly_summary:
            columns += ",progress.source_payload,progress.feed_kind,to_jsonb(progress)->'actual_basis' AS actual_basis"
        else:
            columns += ",jsonb_build_object('retakeTimeChoice',progress.source_payload->'retakeTimeChoice','original_source_ref',progress.source_payload->>'original_source_ref') AS source_payload"
    segment_payload = "jsonb_build_object('id',s.id)" if training_summary else 'to_jsonb(s)'
    historical_payload = ("jsonb_build_object('source_system',h.source_system,"
        "'source_course_ref',h.source_course_ref,'source_activity_id',h.source_activity_id)"
        if training_summary else 'to_jsonb(h)')
    ksb_cte = '' if catalogue_summary else f''', progress_ksbs AS (
        SELECT k.progress_id,jsonb_agg(k.ksb_code ORDER BY k.position) AS payload
        FROM "Learner".learner_progress_ksbs k
        JOIN canonical p ON p.id=k.progress_id
        WHERE {_ksb_not_excluded()}
        GROUP BY k.progress_id
    )'''
    with projection_stage('ledger_read'):
        records = query(f'''WITH canonical AS (
            SELECT {columns}
            FROM "Learner".learner_progress_entries progress
            WHERE progress.learner_id=%s AND progress.deleted_at IS NULL{selection}
        ){ksb_cte}, reporting_segments AS (
            SELECT s.progress_id,{"'[{}]'::jsonb" if catalogue_summary else f"jsonb_agg({segment_payload} ORDER BY s.segment_order,s.id)"} AS payload
            FROM "Learner".learner_activity_reporting_segments s
            JOIN canonical p ON p.id=s.progress_id AND p.learner_id=s.learner_id
            GROUP BY s.progress_id
        ), activity_sources AS (
            SELECT s.canonical_progress_id AS progress_id,jsonb_agg(jsonb_build_object(
                'source_id',s.id,'source_system',s.source_system,'source_activity_id',s.source_activity_id,
                'completed',s.completed,
                'source_course_ref',c.source_course_ref,'source_course_id',c.id,
                'course_title',c.source_course_title,'catalogue_id',a.id,
                'component_ref',coalesce(nullif(s.curriculum_component_ref,''),a.curriculum_component_ref),
                'module_ref',c.curriculum_module_ref,'group_ref',c.curriculum_group_ref,
                'activity_status',s.activity_status,
                'source_activity_kind',a.source_activity_kind) ORDER BY s.id) AS payload
            FROM "Learner".learner_activity_sources s
            JOIN canonical p ON s.canonical_progress_id=p.id AND s.learner_id=p.learner_id
            LEFT JOIN curriculum.source_activities a ON a.id=s.source_catalog_activity_id AND a.deleted_at IS NULL
            LEFT JOIN curriculum.source_courses c ON c.id=a.source_course_id AND c.deleted_at IS NULL
            WHERE s.deleted_at IS NULL
            GROUP BY s.canonical_progress_id
        ), journal_routes AS (
            SELECT j.progress_id,jsonb_agg(jsonb_build_object(
                'group_id',j.group_id,'activity_id',j.activity_id,'source_ref',j.source_ref,
                'source_system',s.source_system,'month',j.month,'activity_date',j.activity_date) ORDER BY j.id) AS payload,
                sum(j.planned_hours) AS planned
            FROM "Learner".learner_journal_rows j
            JOIN canonical p ON j.progress_id=p.id AND j.canonical_learner_id=p.learner_id
            JOIN "Learner".learner_activity_sources s ON s.id=j.source_id
              AND s.learner_id=p.learner_id AND s.canonical_progress_id=p.id AND s.deleted_at IS NULL
            WHERE j.deleted_at IS NULL
            GROUP BY j.progress_id
        ), historical_components AS (
            SELECT link.progress_id,jsonb_agg({historical_payload} ORDER BY h.id) AS payload
            FROM "Learner".learner_progress_historical_components link
            JOIN canonical p ON p.id=link.progress_id
            JOIN curriculum.historical_components h ON h.id=link.historical_component_ref
              AND h.deleted_at IS NULL AND h.historical_only=true
            GROUP BY link.progress_id
        )
            SELECT {payload} AS payload,
              {"'[]'::jsonb" if catalogue_summary else "coalesce(k.payload,'[]'::jsonb)"} AS ksbs,
              coalesce(seg.payload,'[]'::jsonb) AS segments,
              coalesce(src.payload,'[]'::jsonb) AS sources,
              coalesce(j.payload,'[]'::jsonb) AS journal_routes,
              j.planned AS journal_planned_hours,
              coalesce(h.payload,'[]'::jsonb) AS historical_components
            FROM canonical p
            {'' if catalogue_summary else 'LEFT JOIN progress_ksbs k ON k.progress_id=p.id'}
            LEFT JOIN reporting_segments seg ON seg.progress_id=p.id
            LEFT JOIN activity_sources src ON src.progress_id=p.id
            LEFT JOIN journal_routes j ON j.progress_id=p.id
            LEFT JOIN historical_components h ON h.progress_id=p.id
            ORDER BY p.reporting_month,p.reporting_started_at NULLS LAST,p.id''',
            params)
    from learner_api import journal_sources
    journal_enabled = journal_sources.enabled()
    loaded = []
    for record in records:
        item = {**decoded(record['payload'], {}), **{
            field: decoded(record.get(field), [])
            for field in ('ksbs', 'segments', 'sources', 'journal_routes', 'historical_components')}}
        if journal_enabled and record.get('journal_planned_hours') is not None:
            item['expected_otjh'] = float(record['journal_planned_hours'])
            item['journal_planned_hours'] = float(record['journal_planned_hours'])
        loaded.append(item)
    transfer_state = None
    if owner.get('progress_transfer_active'):
        from .progress_transfer_store import read_state
        from .progress_transfer_policy import frozen_records
        transfer_state = read_state(owner['enrolment_id'], include_records=True)
        loaded = frozen_records(loaded, transfer_state, entry_ids=entry_ids)
    with projection_stage('ledger_reconcile'):
        current = current_records(owner, loaded)
    with projection_stage('ledger_completion'):
        result = progress_records_with_completion(loaded, current)
        if journal_enabled:
            # Completion is independent of accepted hours. Reapply the shared rule
            # after current saves are projected so old-LMS source/status evidence is
            # retained in the journal learner view only.
            for record in result:
                record['completed'] = counts_as_completed(record)
    if transfer_state is not None:
        result = frozen_records(result, transfer_state, entry_ids=entry_ids)
    return result


def weekly_hour_records(owner):
    """Hours-only projection; retain learner-wide facts needed by native dedupe.

    Stored, measured legacy attempts contribute separate evidence without
    changing accepted ledger seconds. Only unsynchronised direct saves need
    current assignment markings. Reporting
    segments are a presence gate here, never additional hour allocations.
    """
    if owner.get('progress_transfer_active'):
        return entries_for(owner, lineage_only=True)
    from .current_learning import project_current, merge_attempts, subject_attempt_rows
    from .canonical_hours import extra_activity_hour_records
    fields = ('id', 'learner_id', 'kind', 'module_ref', 'module_title', 'component_ref',
              'component_type', 'quiz_ref', 'passed', 'reported_time', 'submitted_at', 'declared_completed_at',
              'started_at', 'time_tracking_source', 'claimed_seconds', 'verified_seconds',
              'source_system', 'component_link_source', 'actual_seconds', 'accepted')
    records = query('''SELECT ''' + ','.join('progress.' + field for field in fields) + ''',
        to_jsonb(progress)->>'actual_basis' AS actual_basis,
        CASE WHEN jsonb_typeof(progress.source_payload)='object' THEN
          jsonb_build_object('retakeTimeChoice',progress.source_payload->'retakeTimeChoice','original_source_ref',progress.source_payload->'original_source_ref')
          ELSE progress.source_payload END AS source_payload,
        CASE WHEN EXISTS(SELECT 1 FROM "Learner".learner_activity_reporting_segments s
            WHERE s.progress_id=progress.id AND s.learner_id=progress.learner_id AND s.learner_id=%s)
          THEN '[{}]'::jsonb ELSE '[]'::jsonb END AS segments
        FROM "Learner".learner_progress_entries progress
        WHERE progress.learner_id=%s AND progress.deleted_at IS NULL
          ORDER BY progress.reporting_month,progress.reporting_started_at NULLS LAST,progress.id''', [owner['id'], owner['id']])
    records = [{**record, 'segments': decoded(record.get('segments'), [])} for record in records]
    pending = sorted({str(record['component_ref']) for record in records
                      if record.get('component_ref') and record.get('component_type') == 'assignment'
                      and is_unsynchronised_direct_save(record)})
    markings = {}
    if pending:
        submissions = query('''SELECT component_ref,status,actual_time_hours
            FROM "Learner".learning_reflection_submissions
            WHERE learner_id=%s AND learner_kind=%s AND activity_type IN ('assignment','extra_activity')
              AND component_ref=ANY(%s) ORDER BY submitted_at NULLS FIRST,id''',
            [str(owner['enrolment_id']), owner.get('learner_type'), pending])
        markings = {str(row['component_ref']): row for row in submissions}
    projected = project_current(records, markings)
    projected = merge_attempts(projected, subject_attempt_rows(owner, timed_only=True))
    projected.extend(extra_activity_hour_records([owner])[owner['id']])
    return progress_records_with_completion(records, projected)


def progress_records_with_completion(records, current):
    """Enrich completion while preserving stored hours and historical dates.

    Counts, acceptance, seconds and reporting months belong to progress entries.
    Newer attempts may still supply completion and scores for those same entries.
    Standalone extra activities use their saved coach decision and entered time;
    they have no curriculum plan or separate progress save. Other submissions
    cannot introduce additional accepted-hour records on a read.
    Server-measured, persisted legacy attempts are separate timed evidence;
    their stable placement IDs prevent retries from duplicating those hours.

    The exception is a stored direct LMS save that nothing has given seconds
    yet: its projected time and decision are the only record of that learning,
    so they fill the empty ledger fields rather than leaving it at zero hours.
    """
    by_id = {str(record['id']): record for record in current}
    superseded = {str(ident) for record in current for ident in record.get('retake_superseded_ids', [])}
    result = []
    for record in records:
        if str(record['id']) in superseded:
            continue
        latest = by_id.get(str(record['id']), {})
        fields = ('completed', 'achieved_score', 'total_score')
        if is_unsynchronised_direct_save(record) or latest.get('retake_timing_applied'):
            fields += ('accepted', 'actual_seconds', 'activity_status', 'reporting_month',
                       'reporting_started_at', 'reporting_ended_at', 'reporting_timestamp_label',
                       'retake_timing_applied', 'completion_reporting_date')
        result.append({**record, **{key: latest[key] for key in fields if key in latest}})
    existing = {str(record['id']) for record in records}
    result.extend(({**record, 'hours_only': True} if record.get('actual_basis') == 'extra_activity_input' else record)
                  for record in current
                  if record.get('actual_basis') in {'legacy_activity_verified_timer', 'extra_activity_input'}
                  and str(record['id']) not in existing)
    return result


def allocations(entry):
    """Allocate October-onward submissions by their saved completion instant.

    This is a read projection: original audit timestamps, seconds, acceptance,
    historical allocations and reporting segments are never rewritten.
    """
    if entry.get('retake_timing_applied'):
        return [entry]
    submitted = completion_reporting_instant(entry.get('submitted_at'))
    if not submitted or submitted.strftime('%Y-%m') < '2026-10':
        return [entry]
    # Imported historical activity can carry a later submission/import date.
    if entry.get('reporting_month') and entry['reporting_month'] < '2026-10':
        return [entry]
    completed = completion_reporting_instant(entry.get('declared_completed_at')) or submitted
    started = completion_reporting_instant(entry.get('started_at') or entry.get('reporting_started_at'))
    # A backdated declaration does not establish when the learner started.
    # Show a range only when the saved start belongs to this completion day.
    if not started or started.date() != completed.date() or started.timestamp() > completed.timestamp():
        started = None
    label = completed.strftime('%H:%M:%S')
    if started and started.timestamp() < completed.timestamp():
        label = f"{started:%H:%M:%S} - {completed:%H:%M:%S}"
    return [{**entry, 'reporting_month': completed.strftime('%Y-%m'),
             'reporting_started_at': started, 'reporting_ended_at': completed,
             'reporting_timestamp_label': label,
             'completion_reporting_date': completed.date().isoformat()}]


def completion_reporting_instant(value):
    """Read database instants in business time, never machine-local time."""
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UK)
    return value.astimezone(UK)


def recorded_seconds(entry):
    return sum(number(part.get('actual_seconds')) for part in allocations(entry))


def local_instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.astimezone(UK) if value else None


def activity_rows(learner_id):
    owner = profile(learner_id)
    if owner is None:
        return []
    return rows_for(owner)


def journal_activity_date(entry):
    """Use an unambiguous original journal date within the progress reporting month."""
    if entry.get('source_system') != 'journal':
        return None
    month = entry.get('reporting_month')
    dates = set()
    for route in entry.get('journal_routes') or []:
        if not isinstance(route, dict) or route.get('source_system') != 'journal' or route.get('month') != month:
            continue
        value = str(route.get('activity_date') or '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or value[:7] != month:
            continue
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            continue
        dates.add(value)
    return next(iter(dates)) if len(dates) == 1 else None


def input_activity_date(entry):
    """A staff Input declaration has a date and no fabricated clock times."""
    if entry.get('actual_basis') != 'staff:module-progress' or entry.get('reporting_timestamp_label') not in {'Input', 'attended'}:
        return None
    metadata = source_payload_metadata(entry.get('source_payload'))
    declared = metadata.get('staff_time_entry')
    value = declared.get('activity_date') if isinstance(declared, dict) and declared.get('mode') == 'input' else None
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None
    return value if parsed.strftime('%Y-%m-%d') == value and value[:7] == entry.get('reporting_month') else None


def rows_for(owner, records=None):
    result = []
    for entry in (part for record in (records if records is not None else entries_for(owner)) for part in allocations(record)):
        source = source_payload_metadata(entry.get('source_payload'))
        at = local_instant(entry.get('reporting_started_at') or entry.get('reporting_ended_at'))
        ref = f"canonical:{entry['id']}"
        if entry.get('segment_id') is not None:
            ref += f":segment:{entry['segment_id']}"
        item = row(ref, at, entry.get('component_title'),
                   (entry.get('component_type') or entry.get('kind') or 'Learning').replace('_', ' ').title(),
                   hours=number(entry.get('actual_seconds')) / 3600,
                   planned=entry.get('expected_otjh') if entry.get('expected_otjh') is not None else source.get('planned_hours'),
                   note=source.get('completion_note') or entry.get('feedback'),
                   group=entry.get('module_title') or entry.get('group_title'), ksbs=entry['ksbs'])
        # A backfill timestamp may describe a later import/edit, not the original
        # activity. Correct only that display date; hours and month stay on progress.
        if not at or at.strftime('%Y-%m') != entry.get('reporting_month'):
            item['activity_date'] = journal_activity_date(entry) or item['activity_date']
        timestamp_label = entry.get('reporting_timestamp_label') or source.get('timestamp_label') or ''
        declared_date = input_activity_date(entry)
        if declared_date:
            item['activity_date'] = declared_date
        if entry.get('completion_reporting_date'):
            item['activity_date'] = entry['completion_reporting_date']
        # Translate the historical system label for display without rewriting the ledger.
        if timestamp_label == '\u062a\u0642\u062f\u064a\u0631\u064a \u2014 \u064a\u062d\u062a\u0627\u062c \u0627\u0639\u062a\u0645\u0627\u062f':
            timestamp_label = 'Estimated — approval required'
        item.update(accepted=entry.get('accepted') is True,
                    source_system=entry.get('source_system'),
                    reporting_month=entry.get('reporting_month'),
                    timestamp_label=timestamp_label,
                    actual_hours_recorded=entry.get('actual_seconds') is not None,
                    actual_estimated=is_time_estimated(entry),
                    actual_status_label='Estimated — approval required' if is_time_estimated(entry)
                    else ('Accepted' if entry.get('accepted') is True else 'Not accepted'),
                    progress_id=entry['id'])
        result.append(item)
    by_id = {}
    for item in result:
        by_id.setdefault(item['progress_id'], []).append(item)
    documents = query('''SELECT id,progress_id,display_name,content_type
        FROM "Learner".learner_activity_documents
        WHERE learner_id=%s AND deleted_at IS NULL ORDER BY id''', [owner['id']])
    for document in documents:
        for item in by_id.get(document['progress_id'], []):
            item['documents'].append({'id': document['id'], 'display_name': document['display_name'],
                'content_type': document['content_type'],
                'url': f"/learner_api/monthly-logs/{owner['enrolment_id']}/canonical-documents/{document['id']}/" if owner.get('enrolment_id') else None})
    return result


def targets(learner_id):
    owner = profile(learner_id)
    if owner is None:
        return {}
    return targets_for(owner)


def targets_for(owner, *, month=None):
    """Return one exact Training Plan target per month for this enrolment."""
    month_filter = ' AND report_month=%s' if month else ''
    return {record['report_month']: float(record['target_hours']) for record in query(f'''
        SELECT DISTINCT ON (report_month) report_month,target_hours
        FROM "Learner".learner_monthly_targets
        WHERE learner_id=%s
          AND coalesce(programme_profile_id,'')=''
          AND (enrolment_id IS NULL OR enrolment_id IS NOT DISTINCT FROM %s)
          AND (programme_id IS NULL OR programme_id IS NOT DISTINCT FROM %s) {month_filter}
        ORDER BY report_month,
          (enrolment_id IS NOT DISTINCT FROM %s) DESC,
          (programme_id IS NOT DISTINCT FROM %s) DESC,
          updated_at DESC,id DESC''', [
            owner['id'], owner.get('enrolment_id'), owner.get('programme_id'),
            *([month] if month else []), owner.get('enrolment_id'), owner.get('programme_id'),
        ]) if record['target_hours'] is not None}


def curriculum_module_ids_for(owner):
    """Current curriculum module ids linked through the consolidated source catalogue."""
    return [record['module_id'] for record in query('''
        SELECT DISTINCT c.curriculum_module_ref AS module_id
        FROM "Learner".learner_source_course_memberships membership
        JOIN curriculum.source_courses c ON c.id=membership.source_course_id
        WHERE membership.learner_id=%s AND membership.deleted_at IS NULL
          AND c.deleted_at IS NULL AND nullif(c.curriculum_module_ref,'') IS NOT NULL
        ORDER BY c.curriculum_module_ref''', [owner['id']])]


def otjh_activities(records):
    """Project the complete accepted SSOT ledger for the OTJ activity log."""
    result = []
    for record in records:
        if not counts_as_actual(record):
            continue
        parts = allocations(record)
        for index, part in enumerate(parts):
            seconds = number(part.get('actual_seconds'))
            if seconds <= 0:
                continue
            at = local_instant(part.get('reporting_started_at') or part.get('reporting_ended_at'))
            result.append({
                'id': f"{record['id']}:{part.get('segment_id') or 0}",
                'kind': record.get('kind') or 'learning',
                'componentId': record.get('curriculum_component_ref') or record.get('component_ref'),
                'quizId': record.get('quiz_ref'),
                'componentTitle': record.get('component_title') or 'Activity',
                'componentType': record.get('component_type') or record.get('kind') or 'learning',
                'moduleTitle': record.get('module_title') or record.get('group_title'),
                'actualSeconds': seconds,
                'expectedOtjh': record.get('expected_otjh') if index == 0 else None,
                'submittedAt': at.isoformat() if at else None,
                'passed': record.get('passed'),
                'ksbs': list(record.get('ksbs') or []),
            })
    return result


def signatures(learner_id):
    owner = profile(learner_id)
    if owner is None:
        return []
    return signatures_for(owner)


def signatures_for(owner):
    return query('''SELECT report_month,signer_role,signer_name,signed_at,snapshot_digest AS snapshot_hash,
        coalesce(signature_url,signature_data) AS url
        FROM "Learner".learner_monthly_signatures
        WHERE learner_id=%s AND review_confirmed IS TRUE ORDER BY signed_at DESC,id DESC''', [owner['id']])


def content_row(owner, month, row_id):
    """Resolve a preview without rebuilding signatures, targets or month totals."""
    records = entries_for(owner)
    item = next((r for r in rows_for(owner, records)
                 if r['id'] == row_id and str(r.get('reporting_month')) == month), None)
    if item is None:
        raise ServiceError('Activity not found.', 'not_found', 404)
    record = next(r for r in records if r['id'] == item['progress_id'])
    return item, record


def material_parts(item, owner, record=None):
    """Resolve owned canonical activity routes to available private Azure files."""
    from django.conf import settings
    from .material_storage import read_url

    if record is None:
        record = next((r for r in entries_for(owner)
                       if str(r['id']) == str(item.get('progress_id'))), None)
    if record is None:
        return []
    catalogue = query('''SELECT a.source_activity_id,a.source_activity_title,a.source_activity_type,
        a.source_section_title,a.source_position AS position,a.curriculum_component_ref,
        c.source_course_ref,c.source_course_title,c.curriculum_module_ref,
        m.id AS material_record_id,m.title AS material_title,m.backup_status,
        m.blob_container AS material_blob_container,m.blob_name AS material_blob_name,
        m.blob_content_type AS material_blob_content_type,
        m.source_metadata->>'azure_storage_account' AS material_blob_account
        FROM "Learner".learner_source_course_memberships membership
        JOIN curriculum.source_courses c ON c.id=membership.source_course_id
          AND c.deleted_at IS NULL AND c.source_system='old_lms'
        JOIN curriculum.source_activities a ON a.source_course_id=c.id
          AND a.deleted_at IS NULL AND a.source_system=c.source_system
          AND a.source_activity_kind='material'
        JOIN curriculum.source_materials m ON m.material_id=a.source_material_id
          AND m.source_system=a.source_system AND m.deleted_at IS NULL
        WHERE membership.learner_id=%s AND membership.deleted_at IS NULL
          AND c.source_course_ref ~ '^[0-9]+$'
        ORDER BY c.id,a.id''', [owner['id']])
    courses = list({r['source_course_ref']: {
        'source_course_ref': r['source_course_ref'], 'source_course_title': r['source_course_title']}
        for r in catalogue}.values())
    files = {(str(r['source_course_ref']), r['source_activity_id']): r for r in catalogue}
    sources = [r for r in record.get('sources') or []
               if r.get('source_system') == 'old_lms'
               and r.get('source_course_ref') and r.get('source_activity_id')]
    if sources:
        # Preserve the activity namespace: quiz:10 must never open material:10.
        keys = {recorded_source_key(r) for r in sources}
    else:
        component_ids = {r.get('curriculum_component_ref') or r.get('component_ref')
                         for r in [record, *(record.get('sources') or [])]}
        component_ids.discard(None)
        component_ids.discard('')
        # journal:<id> is a source-row identity, not a curriculum component.
        component_ids = {value for value in component_ids if not str(value).startswith('journal:')}
        if component_ids:
            keys = {key for key, material in files.items()
                    if material.get('curriculum_component_ref') in component_ids}
        else:
            placements, _, _ = recorded_course_items(courses, catalogue, [record])
            keys = {(str(p['group_id']), f"{p['catalogue_kind']}:{p['source_activity_id']}")
                    for p in placements}
    # A preview opens only the clicked record's uniquely identified material.
    # Never substitute sibling resources, even when their titles match.
    if len(keys) != 1 or not keys.issubset(files):
        return []
    parts, seen = [], set()
    for key in keys:
        material = files.get(key)
        if material is None:
            continue
        ident = material['material_record_id']
        if ident in seen:
            continue
        seen.add(ident)
        part = {'id': ident, 'title': material['material_title'] or item['title'],
                'category': item['category'], 'content_type': material['material_blob_content_type'],
                'url': None, 'html': None, 'quiz': None}
        if (material['backup_status'] == 'available' and material['material_blob_container']
                and material['material_blob_name']):
            try:
                part['url'] = read_url(material, settings)
            except ValueError as error:
                raise ServiceError('Material storage is temporarily unavailable.', 'storage_unavailable', 503) from error
        else:
            part['html'] = '<p>This material is linked to the activity. Its Azure backup is not ready yet.</p>'
        from .source_material_content import material_quiz_definition
        payload_rows = query('SELECT payload FROM curriculum.source_materials WHERE id=%s AND deleted_at IS NULL', [ident])
        payload = decoded(payload_rows[0].get('payload') if payload_rows else None, {})
        reading = next((payload.get(key) for key in ('reading_text_body', 'text_body', 'html_body')
                        if isinstance(payload.get(key), str) and payload[key].strip()), None)
        if reading:
            part['html'] = reading
        definition = material_quiz_definition(payload)
        if definition:
            part['quiz'] = {'state': 'unavailable', 'attempt': None,
                            'answers_available': False, 'definition': definition}
        parts.append(part)
    return parts


def component_parts(item, owner, record):
    """Read authored local content through the learner's exact progress identity."""
    if record is None:
        return []
    quizzes = query('''SELECT DISTINCT q.id,q.title,q.short_description,q.show_correct_answer
        FROM "Learner".learner_progress_entries p
        JOIN curriculum.components c ON c.id=p.component_ref AND c.deleted_at IS NULL
        JOIN curriculum.quiz_component_links link ON link.component_id=c.id
        JOIN curriculum.quizzes q ON q.id=link.quiz_id
        WHERE p.id=%s AND p.learner_id=%s AND p.deleted_at IS NULL
          AND (nullif(p.quiz_ref,'') IS NULL OR q.id::text=p.quiz_ref)''',
        [record['id'], owner['id']])
    parts = []
    if len(quizzes) == 1:
        quiz = quizzes[0]
        questions = query('''SELECT q.id AS question_id,q.sort_order AS question_order,q.question_text,q.question_type,
            coalesce((SELECT jsonb_agg(jsonb_build_object('option_id',a.id,'option_text',a.answer_text)
                ORDER BY a.sort_order,a.id) FROM curriculum.quiz_answers a WHERE a.question_id=q.id),'[]'::jsonb) AS answer_options
            FROM curriculum.quiz_questions q WHERE q.quiz_id=%s AND coalesce(q.is_archived,false)=false
            ORDER BY q.sort_order,q.id''', [quiz['id']])
        for question in questions:
            options = decoded(question.get('answer_options') or [], None)
            if not isinstance(options, list) or any(not isinstance(option, dict) for option in options):
                raise ServiceError('Quiz answer options could not be read.', 'invalid_quiz_options', 503)
            question['answer_options'] = options
        if questions:
            answers = query('''SELECT a.question_ref,a.chosen_answer_ref,a.is_correct,
                coalesce((SELECT jsonb_agg(s.answer_ref) FROM "Learner".learner_quiz_chosen_answers s
                    WHERE s.quiz_answer_id=a.id),'[]'::jsonb) AS selected,
                coalesce((SELECT jsonb_agg(s.answer_ref) FROM "Learner".learner_quiz_correct_answers s
                    WHERE s.quiz_answer_id=a.id),'[]'::jsonb) AS correct
                FROM "Learner".learner_quiz_answers a
                JOIN "Learner".learner_progress_entries p ON p.id=a.progress_id
                WHERE p.id=%s AND p.learner_id=%s AND p.deleted_at IS NULL ORDER BY a.position''',
                [record['id'], owner['id']])
            answered = {str(a['question_ref']): a for a in answers}
            saved = []
            for question in questions:
                answer = answered.get(str(question['question_id']))
                if answer is None:
                    continue
                selected = {str(value) for value in decoded(answer.get('selected'), [])}
                if answer.get('chosen_answer_ref') is not None:
                    selected.add(str(answer['chosen_answer_ref']))
                correct = {str(value) for value in decoded(answer.get('correct'), [])} if quiz.get('show_correct_answer') else set()
                options = question['answer_options']
                saved.append({**question, 'is_correct': answer.get('is_correct'),
                    'learner_selected_answers': [a['option_text'] for a in options if str(a['option_id']) in selected],
                    'correct_answers': [a['option_text'] for a in options if str(a['option_id']) in correct],
                    'answer_options': [{'option_text': a['option_text'], 'is_selected': str(a['option_id']) in selected} for a in options]})
            attempt = None
            if saved:
                attempt = {'title': quiz['title'], 'status': 'passed' if record.get('passed') else 'submitted',
                    'score': record.get('achieved_score'), 'maximum_score': record.get('total_score'),
                    'attempt_number': record.get('attempt'),
                    'quiz_body': {'description': quiz.get('short_description'), 'questions': saved}}
            parts.append({'id': item['id'], 'title': quiz['title'], 'category': item['category'],
                          'url': None, 'html': None, 'quiz': {'state': 'attempted' if attempt else 'unavailable',
                          'attempt': attempt, 'answers_available': bool(attempt), 'definition': {
                              'description': quiz.get('short_description'), 'questions': questions}}})
    components = query('''SELECT c.description,c.settings_json FROM "Learner".learner_progress_entries p
        JOIN curriculum.components c ON c.id=p.component_ref AND c.deleted_at IS NULL
        WHERE p.id=%s AND p.learner_id=%s AND p.deleted_at IS NULL''', [record['id'], owner['id']])
    if components:
        component = components[0]
        settings = decoded(component.get('settings_json'), {})
        html = settings.get('readingContent') or settings.get('assignmentBrief')
        if html:
            parts.append({'id': -2, 'title': item['title'], 'category': item['category'],
                          'url': None, 'html': html, 'quiz': None})
    return parts


def source_quiz_parts(item, owner, record):
    """Resolve imported quiz definitions by stored quiz ID within an owned course."""
    if record is None:
        return []
    rows = query('''WITH routes AS (
        SELECT a.source_course_id,a.source_activity_id
        FROM "Learner".learner_activity_sources s
        JOIN curriculum.source_activities a ON a.id=s.source_catalog_activity_id
          AND a.source_activity_kind='quiz' AND a.source_system='old_lms' AND a.deleted_at IS NULL
        WHERE s.canonical_progress_id=%s AND s.learner_id=%s AND s.deleted_at IS NULL
        UNION
        SELECT c.id,a.source_activity_id
        FROM "Learner".learner_journal_rows j
        JOIN "Learner".learner_activity_sources s ON s.id=j.source_id
          AND s.canonical_progress_id=j.progress_id AND s.learner_id=j.canonical_learner_id AND s.deleted_at IS NULL
        JOIN curriculum.source_courses c ON c.source_course_ref=j.group_id::text
          AND c.source_system='old_lms' AND c.deleted_at IS NULL
        JOIN curriculum.source_activities a ON a.source_course_id=c.id
          AND a.source_activity_id='quiz:'||j.activity_id::text AND a.source_activity_kind='quiz'
          AND a.source_system=c.source_system AND a.deleted_at IS NULL
        WHERE j.progress_id=%s AND j.canonical_learner_id=%s AND j.deleted_at IS NULL
          AND j.source_ref='la:'||j.group_id::text||':'||j.activity_id::text)
        SELECT DISTINCT m.id,m.payload
        FROM routes r
        JOIN "Learner".learner_source_course_memberships member ON member.source_course_id=r.source_course_id
          AND member.learner_id=%s AND member.deleted_at IS NULL
        JOIN curriculum.source_activities material ON material.source_course_id=r.source_course_id
          AND material.source_activity_kind='material' AND material.source_system='old_lms' AND material.deleted_at IS NULL
        JOIN curriculum.source_materials m ON m.material_id=material.source_material_id
          AND m.source_system=material.source_system AND m.deleted_at IS NULL
          AND 'quiz:'||(m.payload->>'quiz_id')=r.source_activity_id''',
        [record['id'], owner['id'], record['id'], owner['id'], owner['id']])
    if len(rows) != 1:
        return []
    from .source_material_content import material_quiz_definition
    definition = material_quiz_definition(rows[0]['payload'])
    if not definition:
        return []
    return [{'id': item['id'], 'title': item['title'], 'category': item['category'],
             'url': None, 'html': None, 'quiz': {'state': 'unavailable', 'attempt': None,
             'answers_available': False, 'definition': definition}}]


def content(item, owner=None, record=None):
    if owner is not None and record is None:
        record = next((r for r in entries_for(owner) if str(r['id']) == str(item.get('progress_id'))), None)
    if owner is not None and record is not None and record.get('actual_basis') == 'legacy_activity_verified_timer':
        # This evidence belongs to a persisted subject attempt, not a numeric
        # learner_progress_entries ID. Keep its preview in that owned store.
        from .monthly_log_sources import _answers_html
        evidence = source_payload_metadata(record.get('source_payload'))
        attempts = query('''SELECT definition,answers FROM "Learner".subject_activity_attempts
            WHERE id=%s AND enrolment_id=%s AND aptem_id=%s AND submitted_at IS NOT NULL''',
            [evidence.get('subject_attempt_id'), owner['enrolment_id'], owner.get('aptem_id')])
        if not attempts:
            raise ServiceError('Activity not found.', 'not_found', 404)
        saved = attempts[0]
        seconds = int(record.get('actual_seconds') or 0)
        html = f'<p>Measured study time: {seconds // 60}m {seconds % 60}s. Activity completed.</p>'
        html += _answers_html(decoded(saved['definition'], {}), decoded(saved['answers'], {}))
        return {'id': item['id'], 'parts': [{'id': item['id'], 'title': item['title'],
            'category': item['category'], 'url': None, 'quiz': None, 'html': html}]}
    parts = material_parts(item, owner, record) if owner is not None and record is not None else []
    if not parts and owner is not None and record is not None:
        parts = component_parts(item, owner, record)
    if not parts and owner is not None and record is not None:
        parts = source_quiz_parts(item, owner, record)
    note = item.get('completion_note')
    if parts:
        if note:
            parts.append({'id': -1, 'title': 'Activity notes', 'category': item['category'],
                          'url': None, 'quiz': None, 'html': f'<p>{escape(note)}</p>'})
        return {'id': item['id'], 'parts': parts}
    return {'id': item['id'], 'parts': [{'id': item['id'], 'title': item['title'],
        'category': item['category'], 'url': None, 'quiz': None,
        'html': f'<p>{escape(note)}</p>' if note else '<p>No material is directly linked to this activity.</p>'}]}


def owned_source_courses(owner, *, course_ids=None, module_ids=None):
    """Owned old-LMS memberships; optional selection never replaces ownership."""
    scoped = course_ids is not None or module_ids is not None
    selection = ' AND (c.source_course_ref=ANY(%s) OR c.curriculum_module_ref=ANY(%s))' if scoped else ''
    params = [owner['id']]
    if scoped:
        params.extend([[str(value) for value in (course_ids or [])], list(module_ids or [])])
    courses = query(f'''SELECT DISTINCT c.id,c.source_course_ref,c.source_course_title,c.curriculum_module_ref,
        CASE WHEN m.source_relationship='historical'
            THEN m.source_payload->'verified_catalogue_scope' END AS verified_catalogue_scope
        FROM "Learner".learner_source_course_memberships m
        JOIN curriculum.source_courses c ON c.id=m.source_course_id AND c.deleted_at IS NULL
        WHERE m.learner_id=%s AND m.deleted_at IS NULL AND c.source_system='old_lms'
          AND c.source_course_ref ~ '^[0-9]+$'{selection}
        ORDER BY c.source_course_title,c.id''', params)
    return courses


def source_subjects(learner_id, summarize, *, owner=None, records=None, overview_only=False, include_totals=True, include_ledger=True,
                    course_ids=None, module_ids=None):
    """Owned course catalogue with progress, or a compact recorded overview."""
    from learner_api.course_catalogue_display import read_export_placements, catalogue_display_context
    owner = owner if owner is not None else profile(learner_id)
    if owner is None:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    # Filter membership before loading a selected module's catalogue. Keep the
    # complete canonical fact set supplied by the caller for lineage/dedupe.
    courses = owned_source_courses(owner, course_ids=course_ids, module_ids=module_ids)
    catalogue_columns = ("a.source_activity_id,c.source_course_ref" if overview_only else
        '''a.id,a.source_course_id,a.source_activity_id,a.source_activity_kind,
        a.source_activity_title,a.source_activity_type,a.source_section_ref,a.source_section_title,a.curriculum_component_ref,
        c.source_course_ref,c.source_course_title,c.curriculum_module_ref,a.source_position AS position,
        jsonb_build_object('quiz_id',a.source_payload->'quiz_id',
            'activity_id',a.source_payload->'activity_id','parent_activity_id',a.source_payload->'parent_activity_id',
            'section_title',a.source_payload->'section_title',
            'section_order',coalesce(a.source_payload->'section_order',a.source_payload#>'{live_catalogue_recovery,section_order}'),
            'component_order',coalesce(a.source_payload->'component_order',a.source_payload#>'{live_catalogue_recovery,component_order}'),
            'created_at',a.source_payload->'created_at','created_at_utc',a.source_payload->'created_at_utc') AS source_payload,
        EXISTS (SELECT 1 FROM curriculum.source_materials m
            WHERE m.material_id=a.source_material_id AND m.source_system=a.source_system
              AND m.deleted_at IS NULL) AS material_available''')
    catalogue = query(f'''SELECT {catalogue_columns}
        FROM curriculum.source_activities a
        JOIN curriculum.source_courses c ON c.id=a.source_course_id AND c.deleted_at IS NULL
        WHERE a.source_course_id=ANY(%s) AND a.deleted_at IS NULL
          AND a.source_system='old_lms'
        ORDER BY c.id,a.source_position NULLS LAST,a.id''', [[c['id'] for c in courses]]) if courses else []
    if not overview_only:
        catalogue = catalogue_display_context(catalogue, read_export_placements(query, courses))
    records = entries_for(owner) if records is None else records
    items, subjects, links = recorded_course_items(courses, catalogue, records,
        overview_only=overview_only, include_catalogue=not overview_only)
    if overview_only:
        return {'progress_basis': 'recorded_activities', 'subjects': subjects,
                'activities': [{key: item[key] for key in ('activity_id', 'group_id', 'completed')}
                               for item in items]}
    result = {'source': 'canonical', 'source_status': 'historical',
        'progress_basis': 'catalogue_activities',
        'learner_name': owner['name'], 'aptem_id': owner['aptem_id'],
        'subjects': subjects, **summarize(items)}
    if not include_totals:
        result['activity_sources'] = {key: values[0] for key, values in links.items() if len(values) == 1}
        return result
    total = round(sum(recorded_seconds(r) for r in records if counts_as_actual(r)) / 3600, 4)
    result.update(recorded_otjh_total=total, audit_lms_actual=total,
        audit_ksb_evidenced=len({k for r in records if counts_as_actual(r) for k in r['ksbs']}),
        direct_otjh_activities=[])
    if include_ledger:
        result['canonical_otjh_activities'] = otjh_activities(records)
    result['activity_sources'] = {key: values[0] for key, values in links.items() if len(values) == 1}
    result['activity_source_issues'] = {key: 'ambiguous_lineage' for key, values in links.items() if len(values) != 1}
    canonical_modules = {
        str(record.get('module_ref') or record.get('module_title') or '').strip()
        for record in records
        if str(record.get('module_ref') or record.get('module_title') or '').strip()
    }
    result['module_count'] = max(len(subjects), len(canonical_modules))
    result['unresolved_source_routes'] = sum(len(values) != 1 for values in links.values())
    from learner_api import journal_sources
    if journal_sources.enabled():
        result['audit_tp_planned'] = programme_planned_hours(learner_id, owner=owner)
    else:
        monthly_targets = targets_for(owner)
        result['audit_tp_planned'] = sum(monthly_targets.values()) if monthly_targets else None
    from coach_api.migrated_otjh import planned_hours_bulk
    review_planned = planned_hours_bulk([owner]).get(owner['enrolment_id'], 0)
    if review_planned:
        result['review_planned_otjh'] = review_planned
        if result.get('audit_tp_planned') is not None:
            result['audit_tp_planned'] += review_planned
    return result


def recorded_activity_schedule(record, course):
    """Display owned course placement without rewriting reporting timestamps."""
    from datetime import timedelta

    at = local_instant(record.get('reporting_started_at') or record.get('reporting_ended_at'))
    day = at.date() if at else None
    monday = day - timedelta(days=day.weekday()) if day else None
    placement = source_payload_metadata(record.get('source_payload')).get('course_display_placement')
    introduction = (isinstance(placement, dict)
        and str(placement.get('course_id')) == str(course)
        and placement.get('section') == 'introduction')
    return {'date': day.isoformat() if day else None,
        'month': record.get('reporting_month'),
        'week_start': monday.isoformat() if monday else None,
        'week_end': (monday + timedelta(days=6)).isoformat() if monday else None,
        'date_source': 'introduction' if introduction else 'consolidated_record'}


def recorded_source_key(source):
    """Resolve a course-qualified legacy identity only inside its stored route.

    Older imports used one global material/quiz key per learner. A component
    reused by two courses needs distinct source rows, while catalogue IDs and
    learner-facing material links retain the original typed component ID.
    """
    course = str(source.get('source_course_ref') or '')
    reference = str(source.get('source_activity_id') or '')
    if reference.startswith('course:'):
        match = re.fullmatch(r'course:([1-9]\d*):((?:material|quiz):[1-9]\d*)', reference)
        if not match or source.get('source_system') != 'old_lms' or match[1] != course:
            return None
        reference = match[2]
    return course, reference


def learner_course_catalogue(courses, catalogue):
    """Keep restored legacy components within their verified learner/course scope.

    Explicit exclusions are stored on the owned historical membership. This is
    not a snapshot whitelist: later assignments remain visible by default.
    """
    excluded = set()
    for course in courses:
        scope = course.get('verified_catalogue_scope')
        if scope is None:
            continue
        # Raw SQL returns JSON text under the application's PostgreSQL loaders.
        scope = decoded(scope)
        course_ref = str(course['source_course_ref'])
        if (not isinstance(scope, dict) or scope.get('version') != 1
                or scope.get('basis') != 'verified-legacy-account-inventory'
                or scope.get('course_ref') != course_ref
                or not isinstance(scope.get('excluded_activity_refs'), list)
                or any(not isinstance(ref, str) or not re.fullmatch(r'(?:material|quiz):[1-9]\d*', ref)
                       for ref in scope['excluded_activity_refs'])):
            raise ServiceError('The verified learner course scope needs review.',
                               'catalogue_scope_review_required', 409)
        excluded.update((course_ref, ref) for ref in scope['excluded_activity_refs'])
    return [item for item in catalogue
            if (str(item['source_course_ref']), item['source_activity_id']) not in excluded]


def course_record_placements(definitions, records, *, include_catalogue=False):
    """Canonical course allocation, shared by aggregate and activity readers."""
    for record in records:
        placements = {}
        for source in record.get('sources') or []:
            key = recorded_source_key(source)
            if source.get('source_system') == 'old_lms' and key in definitions:
                placements.setdefault(key if include_catalogue else key[0], definitions[key])
        # Imported journal rows and new attempts retain a typed course/material
        # reference in the consolidated payload. Explicit source links take
        # precedence, even when their course is outside this learner's catalogue.
        has_source_route = any(s.get('source_system') == 'old_lms'
            and s.get('source_course_ref') and s.get('source_activity_id')
            for s in record.get('sources') or [])
        if not has_source_route:
            journals = record.get('journal_routes') or []
            routes = set()
            if journals:
                for journal in journals:
                    group, activity = str(journal.get('group_id') or ''), str(journal.get('activity_id') or '')
                    ref = str(journal.get('source_ref') or '')
                    # The journal's la namespace identifies materials, not
                    # standalone quizzes or attendance with a coincident ID.
                    if group.isdigit() and activity.isdigit() and ref == f'la:{group}:{activity}':
                        routes.add((group, 'material:' + activity))
                    else:
                        routes.add(None)
                key = next(iter(routes)) if len(routes) == 1 else None
                if key is None and None not in routes and len({route[0] for route in routes}) == 1:
                    # A canonical record can merge multiple journal materials
                    # within one course. Retain its documented primary material
                    # and count the canonical hours once, not once per journal row.
                    ref = str(source_payload_metadata(record.get('source_payload')).get('original_source_ref') or '')
                    match = re.fullmatch(r'la:(\d+):(\d+)', ref)
                    primary = (match[1], 'material:' + match[2]) if match else None
                    key = primary if primary in routes else None
            else:
                ref = str(source_payload_metadata(record.get('source_payload')).get('original_source_ref') or '')
                match = re.fullmatch(r'la:(\d+):(\d+)', ref)
                key = (match[1], 'material:' + match[2]) if match else None
            keys = routes if (include_catalogue and journals and routes and None not in routes
                              and len({route[0] for route in routes}) == 1) else {key}
            for route in sorted(keys, key=lambda route: (route != key, str(route))):
                if route in definitions:
                    placements.setdefault(route if include_catalogue else route[0], definitions[route])
        yield record, list(placements.values())


def recorded_course_items(courses, catalogue, records, *, overview_only=False, include_catalogue=False):
    """Allocate a final record once per course, using exact catalogue lineage.

    Historical/component guesses do not substitute for learner source links.
    A quiz and material sharing a numeric ID remain distinct activities.
    """
    from learner_api.course_catalogue_display import retain_learner_schedule
    # Learner-specific fallback dates must not leak into another learner's
    # shared/cached catalogue definitions.
    catalogue = [dict(d) for d in learner_course_catalogue(courses, catalogue)]
    by_course = {str(c['source_course_ref']): [] for c in courses}
    definitions = {(str(d['source_course_ref']), d['source_activity_id']): d for d in catalogue
                   if str(d['source_course_ref']) in by_course}
    definitions_by_course = {ref: [] for ref in by_course}
    for (ref, _), definition in definitions.items():
        definitions_by_course[ref].append(definition)
    catalogue_counts = {}
    for definition in catalogue:
        ref = str(definition['source_course_ref'])
        catalogue_counts[ref] = catalogue_counts.get(ref, 0) + 1
    links = {}
    for record, placements in course_record_placements(definitions, records, include_catalogue=include_catalogue):
        counted_courses = set()
        for definition in placements:
            course = str(definition['source_course_ref'])
            if include_catalogue and not overview_only and not record.get('hours_only'):
                retain_learner_schedule(definition, record,
                    completed=counts_as_completed(record, include_source_evidence='completed' in record))
            if overview_only:
                if record.get('hours_only'):
                    continue
                by_course[course].append({'activity_id': f"record:{course}:{record['id']}",
                    'group_id': int(course),
                    'completed': counts_as_completed(record, include_source_evidence='completed' in record)})
                continue
            kind, _, ident = definition['source_activity_id'].partition(':')
            numeric = ident.isdigit()
            accepted = counts_as_actual(record)
            count_hours = course not in counted_courses
            counted_courses.add(course)
            item = {'activity_id': (f"catalogue:{course}:{definition['source_activity_id']}"
                                    if include_catalogue else f"record:{course}:{record['id']}"),
                'source_activity_id': int(ident) if numeric else 0, 'group_id': int(course),
                'group_name': definition['source_course_title'],
                'activity': record.get('component_title') or definition['source_activity_title'],
                'category': definition.get('source_activity_type') or kind,
                'catalogue_kind': kind, 'can_open_material': numeric and kind == 'material',
                'section_title': definition.get('source_section_title'),
                'position': definition.get('position') or 0,
                **recorded_activity_schedule(record, course),
                'completed': counts_as_completed(record, include_source_evidence='completed' in record),
                'historical_completed': accepted,
                'actual': recorded_seconds(record) / 3600 if accepted and count_hours else 0,
                'hours_mapped': accepted and count_hours and any(p.get('actual_seconds') is not None for p in allocations(record)),
                'planned': record.get('journal_planned_hours', 0) if count_hours else 0,
                'planned_hours_mapped': count_hours and 'journal_planned_hours' in record, 'has_result': True,
                'status': record.get('activity_status'),
                'quiz_score': float(record['achieved_score']) if record.get('achieved_score') is not None else None,
                'quiz_maximum_score': float(record['total_score']) if record.get('total_score') is not None else None}
            if include_catalogue:
                # Preserve the existing primary placement for evidence links.
                item['record_ids'] = [str(record['id'])] if count_hours else []
            by_course[course].append(item)
            component = definition.get('curriculum_component_ref')
            module = definition.get('curriculum_module_ref')
            if component and module and item['can_open_material']:
                link = {'module_id': module, 'group_id': int(course), 'activity_id': int(ident)}
                if link not in links.setdefault(component, []):
                    links[component].append(link)
    subjects, items = [], []
    for course in courses:
        ref = str(course['source_course_ref'])
        activity = by_course[ref]
        course_definitions = definitions_by_course[ref]
        if include_catalogue:
            activity = catalogue_items_with_progress(course_definitions, activity)
            for definition in course_definitions:
                kind, _, ident = definition['source_activity_id'].partition(':')
                component, module = definition.get('curriculum_component_ref'), definition.get('curriculum_module_ref')
                if component and module and kind == 'material' and ident.isdigit():
                    link = {'module_id': module, 'group_id': int(ref), 'activity_id': int(ident)}
                    if link not in links.setdefault(component, []):
                        links[component].append(link)
        if not activity and not include_catalogue:
            continue
        subjects.append({'id': int(ref), 'name': course['source_course_title'],
            'module_id': course.get('curriculum_module_ref'),
            **({} if overview_only else {
                'catalogue_count': catalogue_counts.get(ref, 0),
                'accepted_hours': round(sum(a['actual'] for a in activity), 6)})})
        items.extend(activity)
    return items, subjects, links


def catalogue_items_with_progress(definitions, recorded):
    """Keep one stable card per catalogue component, including unstarted work."""
    from learner_api.course_catalogue_display import apply_catalogue_display
    items = {}
    for definition in definitions:
        course = str(definition['source_course_ref'])
        kind, _, ident = definition['source_activity_id'].partition(':')
        key = f"catalogue:{course}:{definition['source_activity_id']}"
        items[key] = {'activity_id': key, 'source_activity_id': int(ident) if ident.isdigit() else 0,
            'group_id': int(course), 'group_name': definition['source_course_title'],
            'activity': definition['source_activity_title'],
            'category': definition.get('source_activity_type') or kind, 'catalogue_kind': kind,
            'can_open_material': kind == 'material' and ident.isdigit(),
            'section_title': definition.get('source_section_title'), 'position': definition.get('position') or 0,
            'date': None, 'month': None, 'week_start': None, 'week_end': None, 'date_source': 'undated',
            'completed': False, 'historical_completed': False, 'actual': 0, 'planned': 0,
            'hours_mapped': False, 'planned_hours_mapped': False, 'has_result': False,
            'status': 'Not started', 'quiz_score': None, 'quiz_maximum_score': None, 'record_ids': []}
    for record in recorded:
        item = items[record['activity_id']]
        if not item['has_result'] or (bool(record['completed']), record.get('date') or '') >= (
                bool(item['completed']), item.get('date') or ''):
            for field in ('date', 'month', 'week_start', 'week_end', 'date_source', 'status'):
                item[field] = record.get(field)
        for field in ('completed', 'historical_completed', 'hours_mapped', 'planned_hours_mapped', 'has_result'):
            item[field] |= record[field]
        for field in ('actual', 'planned'):
            item[field] += record[field]
        item['record_ids'].extend(value for value in record['record_ids'] if value not in item['record_ids'])
        score, maximum = record['quiz_score'], record['quiz_maximum_score']
        if score is not None and maximum and (not item['quiz_maximum_score']
                or score / maximum > item['quiz_score'] / item['quiz_maximum_score']):
            item['quiz_score'], item['quiz_maximum_score'] = score, maximum
        if item['completed']:
            item['status'] = 'Completed'
    return apply_catalogue_display(list(items.values()), definitions)


def ksb_point_definition(record, code):
    """Only an unambiguous stored link may enrich an activity/code label."""
    matches = {(str(item.get('ksb_definition_id')), item.get('definition_code'))
               for item in record.get('ksb_definitions') or []
               if item.get('ksb_code') == code and item.get('ksb_definition_id') is not None}
    if len(matches) != 1:
        return {'ksbDefinitionId': None, 'definitionCode': None}
    definition_id, definition_code = next(iter(matches))
    return {'ksbDefinitionId': definition_id, 'definitionCode': definition_code}


def point_ratio(done, total):
    """Canonical Student point counts and percentage (also used by Coach)."""
    from learner_api.ksb_points import point_ratio as shared_ratio
    return shared_ratio(done, total)


def read_ksb_point_counts(learner_id):
    """Compact activity/code pairs; acceptance owns points, not activity status.

    Student completion enrichment never replaces stored acceptance or adds
    records (progress_records_with_completion), so no evidence hydration is
    needed to reproduce its KSB counts here.
    """
    return query('''WITH links AS (
        SELECT DISTINCT ON (k.ksb_code,p.id) k.ksb_code AS code,p.id,
            k.ksb_description AS description,k.position,p.accepted,
            (p.activity_status='completed' AND p.accepted IS TRUE) AS achieved
        FROM "Learner".learner_progress_entries p
        JOIN "Learner".learner_progress_ksbs k ON k.progress_id=p.id
        WHERE p.learner_id=%s AND p.deleted_at IS NULL AND k.ksb_code IS NOT NULL
          AND ''' + _ksb_not_excluded() + '''
        ORDER BY k.ksb_code,p.id,k.position
    ) SELECT code,(array_agg(description ORDER BY id,position))[1] AS description,
        count(*) AS "evidenceCount",count(*) FILTER (WHERE achieved) AS completed,
        count(*) FILTER (WHERE accepted IS TRUE) AS "pointsAchieved"
        FROM links GROUP BY code ORDER BY min(id),code''', [learner_id])


def metrics_from_records(records, monthly_targets, *, include_ksb_points=False):
    """Count final activity records once, using accepted evidence only for hours.

    KSB points retain the shared activity/code definition; they are not a claim
    that the learner has achieved the entire programme's KSB framework.
    """
    # A configured review allowance contributes hours, not a taught activity
    # or a KSB achievement. Keep programme completion denominators unchanged.
    records = list(records)
    counted = [item for item in records if item.get('kind') != 'migrated_review' and not item.get('hours_only')]
    accepted = [item for item in records if item.get('accepted') is True]
    completed = [item for item in counted if counts_as_completed(
        item, include_source_evidence='completed' in item)]
    ratio = point_ratio
    codes = {}
    for item in counted:
        for code in set(item.get('ksbs') or []):
            counts = codes.setdefault(code, [0, 0])
            counts[0] += item.get('accepted') is True
            counts[1] += 1
    actual = round(sum(recorded_seconds(item) for item in accepted) / 3600, 4)
    from learner_api import journal_sources
    planned = round(sum(monthly_targets.values()), 4) if monthly_targets or journal_sources.enabled() else None
    result = {
        'migrated': True, 'aptem_planned_total': planned,
        'programme': {**ratio(len(completed), len(counted)), 'historicalCompleted': sum(item.get('accepted') is True for item in counted)},
        'otjh': {'historical': actual, 'new': 0, 'actual': actual,
                 'completed_actual': actual, 'planned': planned},
        'ksb': {**ratio(sum(v[0] for v in codes.values()), sum(v[1] for v in codes.values())),
                'historicalCompleted': sum(v[0] for v in codes.values()),
                'codes': [{'code': code, **ratio(*counts)} for code, counts in sorted(codes.items())]},
        '_ksb_evidence_sources': [],
    }
    if include_ksb_points:
        # Expand the very same activity/code pairs counted above. A completion
        # belongs to this activity, never to every activity sharing its code.
        result['ksb']['points'] = [
            {'activityId': str(item['id']), 'code': code,
             **ksb_point_definition(item, code),
             'completed': item.get('accepted') is True,
             'title': item.get('component_title') or None,
             'type': item.get('component_type') or item.get('kind') or None,
             'module': item.get('module_title') or None,
             'status': item.get('activity_status') or None,
             'source': item.get('source_system') or None,
             'completedAt': (item.get('submitted_at') or item.get('reporting_ended_at'))
                 if item.get('accepted') is True else None,
             'componentId': item.get('component_ref') or None}
            for item in counted for code in sorted(set(item.get('ksbs') or []))
        ]
    return result


def metrics(learner_id, *, owner=None):
    """Return verified canonical dashboard metrics for one learner.

    ``owner`` is this learner's already-verified ``require_profile`` row.
    """
    owner = require_profile(learner_id) if owner is None else owner
    from learner_api import journal_sources
    # Only counts and hours leave here, so raw evidence bodies are not loaded.
    result = metrics_from_records(entries_for(owner, lineage_only=True), targets_for(owner))
    if journal_sources.enabled():
        planned = programme_planned_hours(learner_id, owner=owner)
        result['otjh']['planned'] = planned
        result['aptem_planned_total'] = planned
    from coach_api.migrated_otjh import planned_hours_bulk, include_planned
    return include_planned(result, planned_hours_bulk([owner]).get(owner['enrolment_id'], 0))


def otjh_summary_bulk(learner_ids):
    """Profile OTJH facts in bounded reads, without content/KSB hydration.

    Accepted seconds belong to progress rows, not their source snapshots,
    segments or assignment-only submissions. Standalone extra activities add
    only their reviewed input hours. The plan uses the same scoped monthly
    targets as Monthly Logs and the Coach Profile (not Created_users.Planned_hours).
    Invalid or ambiguous identities remain unavailable, just as in metrics().
    """
    from .canonical_hours import pending_hour_records
    enrolment_ids = list(dict.fromkeys(int(value) for value in learner_ids if value is not None))
    if not enrolment_ids:
        return {}
    candidates = {}
    for owner in query('''SELECT l.id,l.enrolment_id,l.aptem_id,l.programme_id,l.programme,
        l.email,l.start_date,l.end_date,l.learner_type,
        u.id AS account_record_id,u."Email" AS account_email,u.aptem_id AS account_aptem_id,
        EXISTS(SELECT 1 FROM "Learner".progress_transfer_requests t WHERE t.enrolment_id=l.enrolment_id) AS progress_transfer_active
        FROM "Learner".learners l
        LEFT JOIN enrolment."Created_users" u ON u.id=l.enrolment_id
        WHERE l.enrolment_id=ANY(%s)''', [enrolment_ids]):
        candidates.setdefault(int(owner['enrolment_id']), []).append(owner)
    result = {enrolment_id: None for enrolment_id in enrolment_ids}
    owners = {}
    for enrolment_id, matches in candidates.items():
        if len(matches) != 1:
            continue
        try:
            owners[enrolment_id] = _validated_profile(matches[0])
        except ServiceError:
            continue
    if not owners:
        return result
    profile_ids = [int(owner['id']) for owner in owners.values()]
    retake_profiles = {int(item['learner_id']) for item in query('''
        SELECT DISTINCT learner_id FROM "Learner".learner_progress_entries
        WHERE learner_id=ANY(%s) AND deleted_at IS NULL
          AND source_payload->>'retakeTimeChoice' IN ('keep','new')''', [profile_ids])}
    relation = query('SELECT to_regclass(%s) AS name', ['"Learner".subject_activity_attempts'])
    if relation and relation[0].get('name'):
        selected_enrolments = {int(item['enrolment_id']) for item in query('''
            SELECT DISTINCT enrolment_id FROM "Learner".subject_activity_attempts
            WHERE enrolment_id=ANY(%s) AND submitted_at IS NOT NULL AND completed IS TRUE
              AND definition->'studyTime'->>'retakeTimeChoice' IN ('keep','new')''', [list(owners)])}
        retake_profiles.update(int(owner['id']) for key, owner in owners.items() if key in selected_enrolments)
    records = {profile_id: [] for profile_id in profile_ids}
    # Aggregate before transfer: full caseloads can contain many historical rows.
    # Mirror number(): ignore non-finite values and clamp negative seconds to 0.
    for item in query('''SELECT learner_id,
        SUM(CASE WHEN actual_seconds::text IN ('NaN','Infinity','-Infinity')
                 THEN 0 ELSE GREATEST(COALESCE(actual_seconds,0),0) END) AS actual_seconds
        FROM "Learner".learner_progress_entries
        WHERE learner_id=ANY(%s) AND deleted_at IS NULL AND accepted IS TRUE
        GROUP BY learner_id''', [profile_ids]):
        records[int(item['learner_id'])].append({**item, 'accepted': True})
    for profile_id, pending in pending_hour_records(owners.values()).items():
        records[profile_id].extend(pending)
    targets = {profile_id: {} for profile_id in profile_ids}
    for item in query('''SELECT DISTINCT ON (t.learner_id,t.report_month)
        t.learner_id,t.report_month,t.target_hours
        FROM "Learner".learner_monthly_targets t
        JOIN "Learner".learners l ON l.id=t.learner_id
        WHERE t.learner_id=ANY(%s) AND coalesce(t.programme_profile_id,'')=''
          AND (t.enrolment_id IS NULL OR t.enrolment_id IS NOT DISTINCT FROM l.enrolment_id)
          AND (t.programme_id IS NULL OR t.programme_id IS NOT DISTINCT FROM l.programme_id)
        ORDER BY t.learner_id,t.report_month,
          (t.enrolment_id IS NOT DISTINCT FROM l.enrolment_id) DESC,
          (t.programme_id IS NOT DISTINCT FROM l.programme_id) DESC,
          t.updated_at DESC,t.id DESC''', [profile_ids]):
        if item['target_hours'] is not None:
            targets[int(item['learner_id'])][item['report_month']] = float(item['target_hours'])
    for enrolment_id, owner in owners.items():
        profile_id = int(owner['id'])
        if owner.get('progress_transfer_active') or profile_id in retake_profiles:
            records[profile_id] = entries_for(owner, lineage_only=True)
        result[enrolment_id] = {
            'profile_id': profile_id,
            'start_date': owner.get('start_date'), 'end_date': owner.get('end_date'),
            **metrics_from_records(records[profile_id], targets[profile_id])['otjh'],
        }
    from coach_api.migrated_otjh import planned_hours_bulk
    review_hours = planned_hours_bulk(list(owners.values()))
    for enrolment_id, hours in review_hours.items():
        if not hours:
            continue
        result[enrolment_id]['review_planned'] = hours
        if result[enrolment_id]['planned'] is not None:
            result[enrolment_id]['planned'] += hours
    return result


def metrics_bulk(learner_ids, *, learner_workspace=False, include_ksb_points=False):
    """Return canonical metrics for a caseload without per-learner queries.

    Keys present with ``None`` identify a canonical record that failed identity
    validation.  Callers must not fall back to a different identity source for
    those learners; this preserves the single-learner fail-closed behaviour.
    """
    enrolment_ids = list(dict.fromkeys(
        int(value) for value in (learner_ids or []) if value is not None
    ))
    if not enrolment_ids:
        return {}

    owners = query('''SELECT l.id,l.enrolment_id,l.aptem_id,l.programme_id,l.full_name AS name,
        l.programme,l.coach_name,l.coach_email,l.email,l.start_date,l.end_date,l.learner_type,
        u.id AS account_record_id,u."Email" AS account_email,u.aptem_id AS account_aptem_id,
        u."Planned_hours" AS programme_planned_hours,
        EXISTS(SELECT 1 FROM "Learner".progress_transfer_requests t WHERE t.enrolment_id=l.enrolment_id) AS progress_transfer_active
        FROM "Learner".learners l
        LEFT JOIN enrolment."Created_users" u ON u.id=l.enrolment_id
        WHERE l.enrolment_id=ANY(%s)''', [enrolment_ids])
    owner_candidates = {}
    for owner in owners:
        owner_candidates.setdefault(int(owner['enrolment_id']), []).append(owner)

    result = {enrolment_id: None for enrolment_id in owner_candidates}
    valid_owners = {}
    for enrolment_id, candidates in owner_candidates.items():
        if len(candidates) != 1:
            continue
        try:
            valid_owners[enrolment_id] = _validated_profile(candidates[0])
        except ServiceError:
            continue
    if not valid_owners:
        return result

    learner_profile_ids = [int(owner['id']) for owner in valid_owners.values()]
    progress_rows = query('''SELECT p.learner_id AS owner_id,p.id,p.kind,
        p.module_title,p.component_ref,p.component_title,p.component_type,p.quiz_ref,
        p.passed,p.feedback,p.reported_time,p.submitted_at,p.declared_completed_at,p.started_at,
        p.time_tracking_source,p.claimed_seconds,p.verified_seconds,p.expected_otjh,
        p.source_system,p.component_link_source,p.actual_seconds,p.accepted,
        p.reporting_started_at,p.reporting_ended_at,p.reporting_month,
        p.achieved_score,p.total_score,p.activity_status,
        CASE WHEN jsonb_typeof(p.source_payload)='object'
             THEN jsonb_build_object('retakeTimeChoice',p.source_payload->'retakeTimeChoice','original_source_ref',p.source_payload->>'original_source_ref')
             ELSE '{}'::jsonb END AS source_payload
        FROM "Learner".learner_progress_entries p
        WHERE p.learner_id=ANY(%s) AND p.deleted_at IS NULL
        ORDER BY p.learner_id,p.reporting_month,p.reporting_started_at NULLS LAST,p.id''',
        [learner_profile_ids])
    records_by_learner = {profile_id: [] for profile_id in learner_profile_ids}
    records_by_progress = {}
    for payload in progress_rows:
        owner_id = int(payload.pop('owner_id'))
        payload.update(ksbs=[], segments=[], sources=[], historical_components=[])
        payload['completed'] = counts_as_completed(payload)
        records_by_learner.setdefault(owner_id, []).append(payload)
        records_by_progress[int(payload['id'])] = payload

    progress_ids = list(records_by_progress)
    if progress_ids:
        definition_columns = ",to_jsonb(k)->>'ksb_definition_id' AS ksb_definition_id,d.code AS definition_code" if include_ksb_points else ''
        definition_join = "LEFT JOIN curriculum.ksb_definitions d ON d.id::text=to_jsonb(k)->>'ksb_definition_id'" if include_ksb_points else ''
        for item in query(f'''SELECT k.progress_id,k.ksb_code{definition_columns}
            FROM "Learner".learner_progress_ksbs k
            {definition_join}
            WHERE k.progress_id=ANY(%s) AND {_ksb_not_excluded()}
            ORDER BY k.progress_id,k.position''', [progress_ids]):
            record = records_by_progress.get(int(item['progress_id']))
            if record is not None:
                record['ksbs'].append(item['ksb_code'])
                if include_ksb_points:
                    record.setdefault('ksb_definitions', []).append(item)
        for item in query('''SELECT progress_id,to_jsonb(s) AS payload
            FROM "Learner".learner_activity_reporting_segments s
            WHERE progress_id=ANY(%s) ORDER BY progress_id,segment_order,id''', [progress_ids]):
            record = records_by_progress.get(int(item['progress_id']))
            if record is not None:
                record['segments'].append(decoded(item['payload'], {}))
        for item in query('''SELECT s.canonical_progress_id AS progress_id,
            s.id AS source_id,s.source_system,s.source_activity_id,s.completed,
            c.source_course_ref,c.id AS source_course_id,
            c.source_course_title AS course_title,a.id AS catalogue_id,
            coalesce(nullif(s.curriculum_component_ref,''),a.curriculum_component_ref) AS component_ref,
            c.curriculum_module_ref AS module_ref,c.curriculum_group_ref AS group_ref,
            a.source_activity_kind,s.activity_status,s.completed
            FROM "Learner".learner_activity_sources s
            LEFT JOIN curriculum.source_activities a
              ON a.id=s.source_catalog_activity_id AND a.deleted_at IS NULL
            LEFT JOIN curriculum.source_courses c
              ON c.id=a.source_course_id AND c.deleted_at IS NULL
            WHERE s.canonical_progress_id=ANY(%s) AND s.deleted_at IS NULL
            ORDER BY s.canonical_progress_id,s.id''', [progress_ids]):
            progress_id = int(item.pop('progress_id'))
            record = records_by_progress.get(progress_id)
            if record is not None:
                record['sources'].append(item)
        for item in query('''SELECT link.progress_id,to_jsonb(h) AS payload
            FROM "Learner".learner_progress_historical_components link
            JOIN curriculum.historical_components h ON h.id=link.historical_component_ref
              AND h.deleted_at IS NULL AND h.historical_only=true
            WHERE link.progress_id=ANY(%s) ORDER BY link.progress_id,h.id''', [progress_ids]):
            record = records_by_progress.get(int(item['progress_id']))
            if record is not None:
                record['historical_components'].append(decoded(item['payload'], {}))

    current_by_enrolment = current_records_bulk(valid_owners.values(), records_by_learner)
    target_rows = query('''SELECT DISTINCT ON (t.learner_id,t.report_month)
        t.learner_id,t.report_month,t.target_hours
        FROM "Learner".learner_monthly_targets t
        JOIN "Learner".learners l ON l.id=t.learner_id
        WHERE t.learner_id=ANY(%s) AND coalesce(t.programme_profile_id,'')=''
          AND (t.enrolment_id IS NULL OR t.enrolment_id IS NOT DISTINCT FROM l.enrolment_id)
          AND (t.programme_id IS NULL OR t.programme_id IS NOT DISTINCT FROM l.programme_id)
        ORDER BY t.learner_id,t.report_month,
          (t.enrolment_id IS NOT DISTINCT FROM l.enrolment_id) DESC,
          (t.programme_id IS NOT DISTINCT FROM l.programme_id) DESC,
          t.updated_at DESC,t.id DESC''', [learner_profile_ids])
    targets_by_learner = {profile_id: {} for profile_id in learner_profile_ids}
    for target in target_rows:
        if target.get('target_hours') is not None:
            targets_by_learner.setdefault(int(target['learner_id']), {})[target['report_month']] = float(target['target_hours'])

    for enrolment_id, owner in valid_owners.items():
        records = progress_records_with_completion(
            records_by_learner.get(int(owner['id']), []),
            current_by_enrolment.get(enrolment_id, []),
        )
        if learner_workspace:
            # Match entries_for's learner journal completion rule, including
            # completed source activities that are excluded from OTJ hours.
            for record in records:
                record['completed'] = record.get('completed', record.get('accepted')) is True or any(
                    source.get('source_system') == 'old_lms' and source.get('completed') is True
                    for source in record.get('sources') or [])
        if owner.get('progress_transfer_active'):
            records = entries_for(owner, lineage_only=True)
        result[enrolment_id] = metrics_from_records(
            records,
            targets_by_learner.get(int(owner['id']), {}),
            include_ksb_points=include_ksb_points,
        )
        if learner_workspace:
            # The learner Overview uses the enrolment's programme plan, not
            # the sum of monthly targets. Reuse the already-validated owner.
            planned = programme_planned_hours(enrolment_id, owner=owner)
            result[enrolment_id]['otjh']['planned'] = planned
            result[enrolment_id]['aptem_planned_total'] = planned
    from coach_api.migrated_otjh import planned_hours_bulk, include_planned
    for enrolment_id, hours in planned_hours_bulk(list(valid_owners.values())).items():
        include_planned(result[enrolment_id], hours)
    return result


def overlay_subjects(payload, records, summarize):
    """Use canonical progress, retaining owned course/material definitions.

    Match explicit namespaced course/activity lineage or a verified Curriculum
    component. Titles and unscoped numeric IDs are not links.
    """
    component_placements = {}
    for item in payload['activities']:
        if item.get('curriculum_component_ref'):
            component_placements.setdefault(item['curriculum_component_ref'], set()).add(
                (int(item['group_id']), int(item['source_activity_id'])))
    mapped = {}
    for record in records:
        keys = set()
        component = record.get('curriculum_component_ref') or record.get('component_ref')
        candidates = component_placements.get(component, set())
        if len(candidates) == 1:
            keys.update(candidates)
        for link in [*(record.get('sources') or []), *(record.get('historical_components') or [])]:
            course = str(link.get('source_course_ref') or '')
            activity = re.fullmatch(r'material:(\d+)', str(link.get('source_activity_id') or ''))
            if link.get('source_system') == 'old_lms' and course.isdigit() and activity:
                keys.add((int(course), int(activity[1])))
            component = link.get('component_ref') or link.get('curriculum_component_ref')
            candidates = component_placements.get(component, set())
            if len(candidates) == 1:
                keys.update(candidates)
        source = source_payload_metadata(record.get('source_payload'))
        match = re.fullmatch(r'la:(\d+):(\d+)', str(source.get('original_source_ref') or ''))
        key = (int(match[1]), int(match[2])) if match else None
        if not keys and key:
            keys.add(key)
        for key in keys:
            mapped.setdefault(key, []).append(record)
    items = []
    for original in payload['activities']:
        item = dict(original)
        matches = mapped.get((int(item['group_id']), int(item['source_activity_id'])), [])
        # Definitions remain available for learning, but imported results must
        # not override the final consolidated status/hours for this rollout.
        item.update(completed=False, historical_completed=False, has_result=bool(matches),
                    actual=0, hours_mapped=False, quiz_score=None, quiz_maximum_score=None,
                    best_score_percent=None, status='Not started')
        if matches:
            latest = max(matches, key=lambda r: (r.get('reporting_month') or '',
                         str(r.get('reporting_ended_at') or r.get('reporting_started_at') or ''), str(r['id'])))
            accepted = [r for r in matches if counts_as_actual(r)]
            recorded = [r for r in accepted if any(part.get('actual_seconds') is not None for part in allocations(r))]
            at = local_instant(latest.get('reporting_started_at') or latest.get('reporting_ended_at'))
            item.update(completed=any(
                counts_as_completed(r, include_source_evidence='completed' in r)
                for r in matches),
                        historical_completed=bool(accepted),
                        actual=sum(recorded_seconds(r) for r in recorded) / 3600,
                        hours_mapped=bool(recorded), status=latest.get('activity_status'),
                        month=latest.get('reporting_month'), date=at.date().isoformat() if at else None,
                        date_source='consolidated_record', week_start=None, week_end=None)
            if item['completed']:
                item['status'] = 'Completed'
            if latest.get('achieved_score') is not None:
                item.update(quiz_score=float(latest['achieved_score']),
                            quiz_maximum_score=float(latest['total_score']) if latest.get('total_score') is not None else None)
        items.append(item)
    result = {**payload, **summarize(items)}
    total = sum(recorded_seconds(r) for r in records if counts_as_actual(r)) / 3600
    result.update(recorded_otjh_total=round(total, 4), audit_lms_actual=round(total, 4),
                  audit_tp_planned=None, audit_ksb_evidenced=len({k for r in records if counts_as_actual(r) for k in r['ksbs']}),
                  direct_otjh_activities=[], canonical_otjh_activities=otjh_activities(records))
    return result
