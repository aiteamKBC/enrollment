"""Original course identities for the learner's assigned imported modules."""


def imported_course_links(rows, current_subjects):
    """Shared exact provenance rule for SQL and request-local module facts."""
    assigned = {str(subject['id']) for subject in current_subjects}
    return {f'current:{module}': f'legacy:{int(course)}'
            for module,course in rows
            if str(module) in assigned and str(course).isdigit() and int(course)>0}


def read_imported_courses(cursor, current_subjects):
    assigned = {str(subject['id']) for subject in current_subjects}
    if not assigned:
        return {}
    cursor.execute('''SELECT module_catalogue_id,source_id
        FROM curriculum.modules
        WHERE module_catalogue_id=ANY(%s) AND source_type='mba-legacy'
          AND (deleted_at IS NULL OR deleted_via_parent IS NOT NULL)
          AND source_id ~ '^[0-9]+$'
        ORDER BY module_catalogue_id''', [sorted(assigned)])
    # Provenance is not enrolment permission. The consumer must also receive
    # the owned legacy course from student-activity before replacing a card.
    return imported_course_links(cursor.fetchall(),current_subjects)


def read_replacement_courses(cursor, current_subjects, enrolment_id, *, include_personal=False):
    """Verified successors; personal links are opt-in for transfer review only."""
    assigned = {str(subject['id']) for subject in current_subjects}
    if not assigned:
        return {}
    cursor.execute('''SELECT DISTINCT COALESCE(personal.module_id,c.curriculum_module_ref) AS module_id,c.source_course_ref
        FROM curriculum.source_courses c
        JOIN "Learner".learner_source_course_memberships m ON m.source_course_id=c.id
        JOIN "Learner".learners l ON l.id=m.learner_id
        CROSS JOIN LATERAL (SELECT CASE
            WHEN %s AND m.source_payload #>> '{verified_replacement,scope}'='learner'
              AND m.source_payload #>> '{verified_replacement,learner_id}'=m.learner_id::text
              AND m.source_payload #>> '{verified_replacement,enrolment_id}'=l.enrolment_id::text
              AND m.source_payload #>> '{verified_replacement,source_course_ref}'=c.source_course_ref
              AND m.source_payload #>> '{verified_replacement,verified_by}' ~ '^[1-9][0-9]*$'
            THEN NULLIF(m.source_payload #>> '{verified_replacement,module_id}','')
            END AS module_id) personal
        WHERE l.enrolment_id=%s AND m.deleted_at IS NULL AND c.deleted_at IS NULL
          AND c.source_system='old_lms'
          AND COALESCE(personal.module_id,c.curriculum_module_ref)=ANY(%s)
          AND (personal.module_id IS NOT NULL OR (
              c.mapping_status='matched' AND c.mapping_method='verified_source_replacement'
              AND c.source_payload #>> '{verified_replacement,module_id}'=c.curriculum_module_ref))
          AND c.source_course_ref ~ '^[0-9]+$'
        ORDER BY module_id,c.source_course_ref''', [include_personal, enrolment_id, sorted(assigned)])
    candidates = {}
    for module, course in cursor.fetchall():
        if str(module) in assigned and str(course).isdigit() and int(course) > 0:
            candidates.setdefault(str(module), set()).add(f'legacy:{int(course)}')
    # Ambiguous replacements stay separate until their identities are reviewed.
    return {f'current:{module}': next(iter(courses))
            for module, courses in candidates.items() if len(courses) == 1}
