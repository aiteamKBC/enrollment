"""Small read-only supplement to stored-hour aggregates for current LMS saves."""
from .current_learning import merge_submissions, project_current


def extra_activity_hour_records(owners):
    """Read standalone extras once, excluding work already in the ledger.

    Enrolment and learner kind jointly own a submission; ledger ownership uses
    the profile ID. Keep the same lineage exclusions as merge_submissions.
    No planned time, evidence hydration or historical writes are needed.
    """
    from . import canonical_learning as canonical

    owners = {int(owner['id']): owner for owner in owners}
    result = {owner_id: [] for owner_id in owners}
    if not owners:
        return result
    values = ','.join(['(%s,%s,%s)'] * len(owners))
    params = [value for owner_id, owner in owners.items()
              for value in (owner_id, str(owner['enrolment_id']), str(owner.get('learner_type') or ''))]
    submissions = canonical.query(f'''WITH owners(profile_id,enrolment_id,learner_kind) AS (VALUES {values})
        SELECT owners.profile_id,s.id,s.component_ref,s.progress_entry_id,s.status,
            s.actual_time_hours,s.submitted_at,s.activity_type,s.activity_title,s.module_title,s.ksb_codes,
            s.full_submission->'monthlyAssignment'->>'month' AS activity_month
        FROM owners JOIN "Learner".learning_reflection_submissions s
          ON s.learner_id=owners.enrolment_id AND s.learner_kind=owners.learner_kind
        WHERE s.activity_type='extra_activity' AND s.status<>'draft' AND s.submitted_at IS NOT NULL
          AND coalesce(s.full_submission->>'submissionOrigin','')<>'imported_legacy'
          AND NOT EXISTS (SELECT 1 FROM "Learner".learner_progress_entries p
            WHERE p.learner_id=owners.profile_id AND p.deleted_at IS NULL
              AND (p.id=s.progress_entry_id OR (nullif(s.component_ref,'') IS NOT NULL AND p.component_ref=s.component_ref)
                OR p.source_payload->>'original_source_ref'='reflection:' || s.id::text))
        ORDER BY s.submitted_at,s.id''', params)
    for record in submissions:
        result[int(record['profile_id'])].append(record)
    return {owner_id: [{**row, 'hours_only': True} for row in merge_submissions([], rows)]
            for owner_id, rows in result.items()}


def pending_hour_records(owners):
    """Use the learner projection without hydrating a caseload's full history.

    Stored seconds (including zero) remain authoritative. Only direct saves
    without stored seconds or reporting segments can contribute extra hours.
    Standalone extra activities are added separately from their coach decisions.
    Other attempts and reflection-only rows cannot introduce ledger rows.
    """
    from . import canonical_learning as canonical

    owners = {int(owner['id']): owner for owner in owners}
    result = extra_activity_hour_records(owners.values())
    if not owners:
        return result
    pending = canonical.query('''SELECT p.id,p.learner_id,p.kind,p.component_ref,
        p.component_type,p.quiz_ref,p.passed,p.reported_time,p.submitted_at,
        p.time_tracking_source,p.claimed_seconds,p.verified_seconds,
        p.component_link_source,p.source_system,p.actual_seconds,p.accepted,
        p.reporting_month
        FROM "Learner".learner_progress_entries p
        WHERE p.learner_id=ANY(%s) AND p.deleted_at IS NULL
          AND p.actual_seconds IS NULL AND coalesce(p.source_system,'')=''
          AND p.component_link_source IN ('direct','quiz_ref')
          AND NOT EXISTS (SELECT 1 FROM "Learner".learner_activity_reporting_segments s
                          WHERE s.progress_id=p.id AND s.learner_id=p.learner_id)
        ORDER BY p.id''', [list(owners)])
    if not pending:
        return result
    affected = sorted({int(record['learner_id']) for record in pending})
    # project_current owns retry/replacement policy. Supply its small amount of
    # stored context so a retained import or staff correction suppresses the
    # original direct save exactly as it does on the learner dashboard.
    context = canonical.query('''SELECT id,learner_id,component_ref,actual_basis,
        actual_seconds,source_system,
        jsonb_build_object('original_source_ref',source_payload->>'original_source_ref') AS source_payload
        FROM "Learner".learner_progress_entries
        WHERE learner_id=ANY(%s) AND deleted_at IS NULL
          AND (coalesce(source_system,'')<>'' OR actual_basis='staff:module-progress')''', [affected])
    assignment_owners = sorted({int(record['learner_id']) for record in pending
                                if record.get('component_type') == 'assignment'})
    markings = {}
    if assignment_owners:
        pairs = [(str(owners[key]['enrolment_id']), str(owners[key].get('learner_type') or ''))
                 for key in assignment_owners]
        placeholders = ','.join(['(%s,%s)'] * len(pairs))
        submissions = canonical.query(f'''SELECT learner_id,learner_kind,component_ref,
            status,actual_time_hours
            FROM "Learner".learning_reflection_submissions
            WHERE (learner_id,learner_kind) IN ({placeholders})
              AND activity_type IN ('assignment','extra_activity')
            ORDER BY submitted_at NULLS FIRST,id''', [value for pair in pairs for value in pair])
        for row in submissions:
            if row.get('component_ref'):
                key = (str(row['learner_id']), str(row['learner_kind']))
                markings.setdefault(key, {})[str(row['component_ref'])] = row
    for owner_id in affected:
        owner = owners[owner_id]
        records = [row for row in pending if int(row['learner_id']) == owner_id]
        pending_ids = {row['id'] for row in records}
        records += [row for row in context if int(row['learner_id']) == owner_id]
        decisions = markings.get((str(owner['enrolment_id']), str(owner.get('learner_type') or '')), {})
        result[owner_id].extend(row for row in project_current(records, decisions) if row['id'] in pending_ids)
    return result
