"""Read-only accepted hours for a verified old course and its current plan."""
from collections import Counter


def project_replacement_hours(records, payload, modules, courses):
    from .training_plan_dashboard import _canonical_module_progress

    owned = {f"legacy:{subject['id']}" for subject in payload.get('subjects', [])}
    assigned = {f"current:{module['id']}" for module in modules}
    uses = Counter(courses.values())
    result = {}
    for current, old in courses.items():
        if current not in assigned or old not in owned or uses[old] != 1:
            continue
        module_id = current.removeprefix('current:')
        # These IDs were allocated by the canonical owned-course catalogue,
        # including exclusions. Titles and completion flags are not time evidence.
        previous_ids = {str(ident) for activity in payload.get('activities', [])
                        if f"legacy:{activity['group_id']}" == old
                        for ident in activity.get('record_ids', [])}
        # Project each canonical row once. A record already counted in the
        # current module must not be added again through its old course route.
        # Copy only the display placement; never rewrite ledger/module history.
        combined = [{**record, 'module_ref': module_id}
                    if str(record['id']) in previous_ids else record for record in records]
        # Retain the dashboard's acceptance, timing and unambiguous title-only
        # fallback rules, with its complete assigned module set.
        hours = _canonical_module_progress(combined, modules)[module_id]['hours']['actual']
        result[current] = {'course_ref': old, 'accepted_hours': hours}
    return result


def read_replacement_hours(enrolment_id, source, owner, records, payload):
    # No owned old course can contribute successor hours. Avoid re-reading the
    # effective assignment and replacement mapping for native-only learners.
    if not payload.get('subjects'):
        return {}
    from django.db import connections
    from .student_activity import _effective_current_subjects
    from .subject_origins import read_replacement_courses
    from .training_plan_dashboard import plan_module_ids

    with connections['enrolment'].cursor() as cursor:
        current = _effective_current_subjects(cursor, enrolment_id)
        courses = read_replacement_courses(cursor, current, enrolment_id)
        if not courses:
            return {}
        # Include all dashboard assignments so a coincident module title cannot
        # capture another module's unlinked quiz time.
        ids, _links = plan_module_ids(cursor, source, owner)
        cursor.execute('''SELECT module_catalogue_id,title FROM curriculum.modules
            WHERE module_catalogue_id=ANY(%s)''', [ids])
        modules = [{'id': ident, 'title': title} for ident, title in cursor.fetchall()]
    return project_replacement_hours(records, payload, modules, courses)
