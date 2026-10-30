"""Explicit learner-owned display groups, independent of completion equivalence."""


def read_previous_learning_groups(cursor, enrolment_id, current_modules):
    assigned = {str(module['id']) for module in current_modules}
    if not assigned:
        return {}
    cursor.execute('''SELECT c.source_course_ref,
            m.source_payload #>> '{learning_display_group,current_module_ref}'
        FROM "Learner".learner_source_course_memberships m
        JOIN "Learner".learners l ON l.id=m.learner_id
        JOIN curriculum.source_courses c ON c.id=m.source_course_id
        WHERE l.enrolment_id=%s AND m.deleted_at IS NULL AND c.deleted_at IS NULL
          AND c.source_system='old_lms'
          AND m.source_payload #>> '{learning_display_group,current_module_ref}'=ANY(%s)
        ORDER BY c.source_course_ref''', [enrolment_id, sorted(assigned)])
    candidates = {}
    for course, module in cursor.fetchall():
        if str(module) in assigned:
            candidates.setdefault(str(course), set()).add(str(module))
    return {f'legacy:{course}': f'current:{next(iter(targets))}'
            for course, targets in candidates.items() if len(targets) == 1}
