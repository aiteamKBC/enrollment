"""Read-only, unpaginated Last_audit activities for a server-resolved Aptem id.

Separate from the mixed attendance/audit feed so pagination cannot silently
drop modules and attendance/assignment hours cannot inflate activity totals.
"""

from html import unescape
from curriculum_api.week_visibility import visible_child_sql
from .source_material_content import hydrate_material

from audit_api.last_audit_ledger_views import _activity_payload, _dict_rows
from audit_api.last_audit_ledger_views import _json_list
from .subject_dates import activity_schedule, apply_section_placement
from .ksb_exclusions import and_not_excluded


def read_activity_sources(cursor, group_ids, module_ids):
    """Exact exported component lineage, limited to this learner's courses/plan."""
    if not group_ids or not module_ids:
        return {}
    cursor.execute('''WITH exports AS (
        SELECT course_id,CASE WHEN jsonb_typeof(curriculum)='string'
            THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
        FROM "MBA".course_curriculum WHERE course_id=ANY(%s))
        SELECT c.id,c.module_catalogue_id,e.course_id,material->>'source_component_id'
        FROM exports e CROSS JOIN LATERAL jsonb_array_elements(payload->'sections') section
        CROSS JOIN LATERAL jsonb_array_elements(section->'materials') material
        JOIN curriculum.components c ON c.id=material->>'component_id'
        WHERE c.module_catalogue_id=ANY(%s)
          AND ''' + visible_child_sql('c'),
                   [sorted(set(group_ids)), sorted(set(module_ids))])
    candidates = {}
    for component, module, group, activity in cursor.fetchall():
        if not activity or not str(activity).isdigit():
            continue
        candidates.setdefault(component, set()).add((module, int(group), int(activity)))
    return {component: {'module_id': value[0], 'group_id': value[1], 'activity_id': value[2]}
            for component, values in candidates.items() if len(values) == 1 for value in values}


def read_activity_source_issues(cursor, group_ids, module_ids):
    """Explain why an assigned Builder component has no unique Aptem lineage."""
    if not module_ids:
        return {}
    cursor.execute('''SELECT id FROM curriculum.components
        WHERE module_catalogue_id=ANY(%s)
          AND ''' + visible_child_sql('components'),
                   [sorted(set(module_ids))])
    component_ids = {str(row[0]) for row in cursor.fetchall()}
    if not group_ids:
        return {component: 'missing_group_id_activity_id' for component in component_ids}
    cursor.execute('''WITH exports AS (
        SELECT course_id,CASE WHEN jsonb_typeof(curriculum)='string'
            THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
        FROM "MBA".course_curriculum WHERE course_id=ANY(%s))
        SELECT material->>'component_id',e.course_id,material->>'source_component_id'
        FROM exports e CROSS JOIN LATERAL jsonb_array_elements(payload->'sections') section
        CROSS JOIN LATERAL jsonb_array_elements(section->'materials') material
        WHERE material->>'component_id'=ANY(%s)''', [sorted(set(group_ids)), sorted(component_ids)])
    rows = {}
    for component, group_id, activity_id in cursor.fetchall():
        if component:
            rows.setdefault(str(component), []).append((group_id, activity_id))
    issues = {}
    for component in component_ids:
        candidates = rows.get(component, [])
        if not candidates:
            issues[component] = 'missing_source_component_id'
            continue
        valid = {(int(group), int(activity)) for group, activity in candidates
                 if group is not None and activity and str(activity).isdigit()}
        if len(valid) > 1:
            issues[component] = 'ambiguous_lineage'
        elif not valid:
            issues[component] = ('missing_source_component_id'
                                 if all(not activity for _, activity in candidates)
                                 else 'missing_group_id_activity_id')
    return issues


CURRICULUM_SCHEDULE_SQL = '''
    WITH exports AS (
        SELECT course_id,
               CASE WHEN jsonb_typeof(curriculum)='string'
                    THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
        FROM "MBA".course_curriculum WHERE course_id=ANY(%s)
    )
    SELECT e.course_id, material->>'source_component_id' AS activity_id,
           section->>'source_section_id' AS section_id,
           section->>'section_title' AS section_title,
           material->>'created_at_utc' AS original_created_at,
           w.id AS builder_week_id, w.title AS builder_week_title
    FROM exports e
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(payload->'sections')='array' THEN payload->'sections' ELSE '[]'::jsonb END
    ) section
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(section->'materials')='array' THEN section->'materials' ELSE '[]'::jsonb END
    ) material
    LEFT JOIN curriculum.modules m ON m.source_type='mba-legacy' AND m.source_id=e.course_id::text
        AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
    LEFT JOIN curriculum.components c ON c.id=material->>'component_id'
        AND c.module_catalogue_id=m.module_catalogue_id
        AND c.deleted_at IS NULL AND NOT coalesce(c.is_programme_deleted,false)
    LEFT JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=m.module_catalogue_id
        AND w.deleted_at IS NULL AND NOT coalesce(w.is_programme_deleted,false)
'''


def read_curriculum_schedules(cursor, group_ids):
    """Read scheduling context by exact course/activity ids, never by title.

    The retained export also covers courses not yet in Module Builder. A linked
    Builder component's current week takes precedence over its exported week.
    Only date metadata leaves this query; no content or download tokens do.
    """
    if not group_ids:
        return {}
    cursor.execute(CURRICULUM_SCHEDULE_SQL, [sorted(set(group_ids))])
    candidates = {}
    for row in _dict_rows(cursor):
        try:
            key = (int(row['course_id']), int(row['activity_id']))
        except (TypeError, ValueError):
            continue
        section = {
            'section_id': row.get('builder_week_id') or row.get('section_id'),
            'source_section_id': row.get('section_id'),
            'section_title': unescape(str((row.get('builder_week_title') if row.get('builder_week_id') else row.get('section_title')) or '')),
            'section_source': 'builder_section_title' if row.get('builder_week_id') else 'section_title',
            'exported_section_title': unescape(str(row.get('section_title') or '')),
            'original_created_at': row.get('original_created_at'),
        }
        matches = candidates.setdefault(key, [])
        if section not in matches:
            matches.append(section)
    # Reused activities in different dated sections are ambiguous, not an
    # invitation to select the first section (or silently use an upload date).
    return {key: matches[0] if len(matches) == 1 else {'ambiguous': True}
            for key, matches in candidates.items()}


def apply_curriculum_schedules(items, schedules):
    for item in items:
        context = schedules.get((item['group_id'], item['source_activity_id']))
        if not context:
            continue
        stored_date = item.get('source_date')
        if context.get('ambiguous'):
            schedule = activity_schedule(item['activity'])
            if schedule['date_source'] == 'undated':
                schedule.update(date_source='section_needs_review', date_needs_review=True)
            item.update(schedule, source_date=stored_date)
            continue
        item.update(activity_schedule(
            item['activity'], stored_date, context.get('original_created_at') or (item.get('date') if item.get('date_source') == 'original_created_at' else None),
            section_title=context.get('section_title'), section_source=context['section_source'],
        ))
        item['section_title'] = context.get('section_title') or ''
        apply_section_placement(item, item['group_id'], context.get('source_section_id'))
    return items


ACTIVITY_SQL = '''
    SELECT gl.group_id, l.learner_id, l.aptem_id, l.learner_name,
           g.group_name, ga.activity_id, ga.position,
           COALESCE(a.activity_type, r.activity_type) AS activity_type,
           a.title, a.activity_date, a.reading_type, a.reading_iframe_url,
           a.quiz_id, a.quiz_questions, r.status, r.activity_id IS NOT NULL AS has_result,
           COALESCE(a.raw #>> '{live_lms_component,created_at}',
                    a.raw #>> '{live_lms_quiz,created_at}') AS original_created_at,
           r.video_started, r.video_completed, r.reading_viewed,
           r.quiz_attempted, r.quiz_passed, r.quiz_score,
           COALESCE(r.quiz_maximum_score,a.quiz_maximum_score) AS quiz_maximum_score,
           r.mapped_seconds, r.mapped_hours,
           ph.planned_hours AS otjh_planned, ah.actual_hours AS otjh_actual
    FROM "Last_audit".learners l
    JOIN "Last_audit".group_learners gl ON gl.learner_id = l.learner_id
    JOIN "Last_audit".group_activities ga ON ga.group_id = gl.group_id
    JOIN "Last_audit".activities a ON a.activity_id = ga.activity_id
    LEFT JOIN "Last_audit".activity_results r ON r.learner_id = l.learner_id
      AND r.group_id = gl.group_id AND r.activity_id = ga.activity_id
    LEFT JOIN "Last_audit".groups g ON g.group_id = gl.group_id
    LEFT JOIN "Last_audit".activity_planned_hours ph
      ON ph.learner_id = l.learner_id AND ph.aptem_id = l.aptem_id
     AND ph.ref = ga.activity_id::text
     AND ph.kind = CASE lower(COALESCE(a.activity_type, r.activity_type))
         WHEN 'video' THEN 'video' WHEN 'audio' THEN 'audio'
         WHEN 'reading+quiz' THEN 'reading_quiz' END
    LEFT JOIN "Last_audit".activity_actual_hours ah
      ON ah.learner_id = l.learner_id AND ah.aptem_id = l.aptem_id
     AND ah.ref = ga.activity_id::text
     AND ah.kind = CASE lower(COALESCE(a.activity_type, r.activity_type))
         WHEN 'video' THEN 'video' WHEN 'audio' THEN 'audio'
         WHEN 'reading+quiz' THEN 'reading_quiz' END
    WHERE l.aptem_id = %s
    ORDER BY a.activity_date NULLS LAST, g.group_name, ga.position, ga.activity_id
'''

ITEM_FIELDS = (
    "activity_id", "source_activity_id", "group_id", "group_name", "date",
    "category", "activity", "status", "completed", "actual", "planned",
    "hours_mapped", "quiz_score", "quiz_maximum_score",
    "video_started", "reading_viewed", "quiz_attempted",
)


def read_student_material(cursor, aptem_id, group_id, activity_id, *, include_source=False, demo_enrolment_id=None):
    # Membership must match BOTH activity and group for this learner.
    identity_sql, identity = 'l.aptem_id = %s', aptem_id
    if demo_enrolment_id is not None:
        # Only explicitly provisioned rehearsals can use enrolment identity.
        # All normal historical readers keep their Aptem gate.
        identity_sql = '''l.enrolment_id = %s AND membership.enrolment_id=l.enrolment_id
            AND membership.source_payload #>> '{demo_progress,kind}'='owner_requested_simulation'
            AND membership.source_payload #>> '{demo_progress,enrolment_id}'=l.enrolment_id::text
            AND membership.source_payload #>> '{demo_progress,course_ref}'=course.source_course_ref'''
        identity = demo_enrolment_id
    cursor.execute('''
        SELECT l.full_name AS learner_name, l.email AS learner_email, a.title, a.video_iframe_url,
               a.reading_iframe_url, a.reading_text_body,
               a.raw #>> '{audio,iframe_url}' AS audio_url,
               a.quiz_body, a.quiz_questions, %s AS activity_id, a.activity_type,
               a.quiz_id, a.quiz_passing_score, a.quiz_maximum_score,
               r.quiz_answers, r.quiz_score, COALESCE(r.quiz_maximum_score,a.quiz_maximum_score) AS result_maximum_score,
               r.quiz_attempt_number, r.quiz_passed, r.reading_viewed, r.status,
               r.video_completed, a.reading_type, a.raw,
               material.title AS material_title,material.content_type AS material_content_type,
               material.payload AS material_payload,material.source_url AS material_source_url,
               material.video_iframe_url AS material_video_url,
               material.reading_iframe_url AS material_reading_url,
               material.audio_iframe_url AS material_audio_url,
               material.blob_container AS material_blob_container,material.blob_name AS material_blob_name,
               material.blob_content_type AS material_blob_content_type,material.backup_status AS material_backup_status,
               material.source_metadata->>'azure_storage_account' AS material_blob_account
        FROM "Learner".learners l
        JOIN "Learner".learner_source_course_memberships membership
          ON membership.learner_id=l.id AND membership.deleted_at IS NULL
        JOIN curriculum.source_courses course ON course.id=membership.source_course_id
          AND course.source_system='old_lms' AND course.deleted_at IS NULL
        JOIN curriculum.source_activities catalogue ON catalogue.source_course_id=course.id
          AND catalogue.source_system='old_lms' AND catalogue.source_activity_kind='material'
          AND catalogue.deleted_at IS NULL
        JOIN curriculum.source_materials material ON material.material_id=catalogue.source_material_id
          AND material.source_system=catalogue.source_system AND material.deleted_at IS NULL
        LEFT JOIN "Last_audit".activities a ON catalogue.source_activity_id='material:' || a.activity_id::text
        LEFT JOIN LATERAL (
            -- Same positive-completion/highest-score policy as merge_result.
            -- Keep score, maximum, attempt number and answers from ONE attempt.
            SELECT history.quiz_answers,history.quiz_score,history.quiz_maximum_score,
                   history.quiz_attempt_number,
                   bool_or(history.quiz_passed) OVER () AS quiz_passed,
                   bool_or(history.reading_viewed) OVER () AS reading_viewed,
                   bool_or(history.video_completed) OVER () AS video_completed,
                   CASE max(CASE history.status WHEN 'completed' THEN 3
                        WHEN 'quiz_attempted' THEN 2 WHEN 'reading_viewed' THEN 1 ELSE 0 END) OVER ()
                     WHEN 3 THEN 'completed' WHEN 2 THEN 'quiz_attempted'
                     WHEN 1 THEN 'reading_viewed' ELSE history.status END AS status
            FROM "Learner".learner_external_identities identity
            JOIN "Last_audit".activity_results history
              ON history.learner_id::text=identity.source_learner_id
            WHERE identity.learner_id=l.id AND identity.source_system='old_lms'
              AND identity.deleted_at IS NULL
              AND history.group_id::text=course.source_course_ref AND history.activity_id=a.activity_id
            ORDER BY history.quiz_score / nullif(history.quiz_maximum_score,0) DESC NULLS LAST,
                     (history.quiz_answers IS NOT NULL AND history.quiz_answers<>'[]'::jsonb) DESC,
                     history.quiz_attempt_number DESC NULLS LAST,identity.id
            LIMIT 1
        ) r ON true
        WHERE DEMO_IDENTITY_FILTER AND course.source_course_ref = %s::text AND catalogue.source_activity_id = %s
        LIMIT 2
    '''.replace('DEMO_IDENTITY_FILTER', identity_sql), [activity_id, identity, group_id, f'material:{activity_id}'])
    rows = _dict_rows(cursor)
    if len(rows) != 1:
        return None
    row = hydrate_material(rows[0])
    # Expose the question and options only, never source grading keys.
    questions = []
    for question in _json_list(row.get("quiz_questions")):
        if not isinstance(question, dict):
            continue
        questions.append({
            "text": question.get("question_body") or "",
            "options": [str(option.get("option_body") or option.get("option_text") or "")
                        for option in _json_list(question.get("options")) if isinstance(option, dict)],
        })
    result = {"learner_name": row["learner_name"], "title": row["title"],
            "video_url": row["video_iframe_url"], "audio_url": row["audio_url"],
            "reading_url": row["reading_iframe_url"], "reading_html": row["reading_text_body"],
            "quiz_description": row["quiz_body"], "questions": questions}
    if include_source:
        result['_source'] = row
    return result


def summarize_activities(items):
    # Results are per group; OTJH is per learner/activity, regardless of group.
    # Keep placements visible but count the same recorded hours only once.
    unique = {}
    for item in items:
        previous = unique.setdefault(item["source_activity_id"], dict(item))
        # A reserved-hours fallback may be populated on only one placement.
        for flag, value in (("hours_mapped", "actual"), ("planned_hours_mapped", "planned")):
            if item[flag] and not previous[flag]:
                previous[flag] = True
                previous[value] = item[value]
    mapped = [item for item in unique.values() if item["hours_mapped"]]
    planned = [item for item in unique.values() if item["planned_hours_mapped"]]
    return {
        "count": len(items),
        "unique_activity_count": len(unique),
        "module_count": len({item["group_id"] for item in items}),
        "completed_count": sum(bool(item["completed"]) for item in items),
        "mapped_count": len(mapped),
        "planned_mapped_count": len(planned),
        "actual_total": round(sum(item["actual"] for item in mapped), 4) if mapped else None,
        "planned_total": round(sum(item["planned"] for item in planned), 4) if planned else None,
        "activities": items,
    }


def read_audit_hour_totals(cursor, aptem_id):
    return read_audit_hour_totals_bulk(cursor, [aptem_id]).get(
        int(aptem_id), {'audit_tp_planned': None, 'audit_lms_actual': None})


def read_audit_hour_totals_bulk(cursor, aptem_ids):
    """Shared coach/learner totals from accepted, non-deleted progress rows."""
    ids = sorted({int(str(value).strip()) for value in aptem_ids or []
                  if str(value or '').strip().isdigit() and int(str(value).strip()) > 0})
    if not ids:
        return {}
    cursor.execute('''SELECT l.aptem_id,
        (SELECT sum(t.target_hours) FROM "Learner".learner_monthly_targets t
          WHERE t.learner_id=l.id) AS planned,
        coalesce((SELECT sum(p.actual_seconds) / 3600.0
          FROM "Learner".learner_progress_entries p
          WHERE p.learner_id=l.id AND p.deleted_at IS NULL AND p.accepted IS TRUE),0) AS actual
        FROM "Learner".learners l WHERE l.aptem_id=ANY(%s)
          AND (SELECT count(*) FROM "Learner".learners other WHERE other.aptem_id=l.aptem_id)=1
        ''', [ids])
    return {int(item[0]): {
        'audit_tp_planned': round(float(item[1]), 2) if item[1] is not None else None,
        'audit_lms_actual': round(float(item[2]), 2) if item[2] is not None else None,
    } for item in cursor.fetchall()}


def read_evidenced_ksb_counts_bulk(cursor, aptem_ids):
    ids = sorted({int(str(value).strip()) for value in aptem_ids or []
                  if str(value or '').strip().isdigit() and int(str(value).strip()) > 0})
    if not ids:
        return {}
    cursor.execute('''SELECT l.aptem_id,count(DISTINCT k.ksb_code)
        FROM "Learner".learners l
        LEFT JOIN "Learner".learner_progress_entries p
          ON p.learner_id=l.id AND p.deleted_at IS NULL AND p.accepted IS TRUE
        LEFT JOIN "Learner".learner_progress_ksbs k ON k.progress_id=p.id''' + and_not_excluded('k', getattr(cursor, 'db', None)) + '''
        WHERE l.aptem_id=ANY(%s)
          AND (SELECT count(*) FROM "Learner".learners other WHERE other.aptem_id=l.aptem_id)=1
        GROUP BY l.aptem_id''', [ids])
    return {int(item[0]): int(item[1]) for item in cursor.fetchall()}


def read_student_activity(cursor, aptem_id):
    cursor.execute('''
        SELECT learner_id, learner_name, learner_email FROM "Last_audit".learners WHERE aptem_id = %s
    ''', [aptem_id])
    learner = cursor.fetchone()
    if learner is None:
        return None
    cursor.execute('''SELECT g.group_id, g.group_name FROM "Last_audit".group_learners gl
                      JOIN "Last_audit".groups g ON g.group_id=gl.group_id
                      JOIN "Last_audit".learners l ON l.learner_id=gl.learner_id
                      WHERE l.aptem_id=%s ORDER BY g.group_name,g.group_id''', [aptem_id])
    subjects = [{'id': row['group_id'], 'name': row.get('group_name') or 'Unnamed subject'} for row in _dict_rows(cursor)]
    cursor.execute(ACTIVITY_SQL, [aptem_id])
    items = []
    for row in _dict_rows(cursor):
        normalized = _activity_payload(row)
        item = {key: normalized[key] for key in ITEM_FIELDS}
        item["planned_hours_mapped"] = row["otjh_planned"] is not None
        item.update(activity_schedule(row.get('title'), row.get('activity_date'), row.get('original_created_at')))
        item['position'] = row.get('position') or 0
        item['has_result'] = row.get('has_result', True)
        items.append(item)
    return {
        "source": "Last_audit",
        "aptem_id": aptem_id,
        "learner_name": learner[1],
        "_identity_email": learner[2] if len(learner) > 2 else '',
        "subjects": subjects,
        **summarize_activities(items),
    }
