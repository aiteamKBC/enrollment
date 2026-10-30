"""Explicit, learner-scoped transfer rehearsals. Never grant ordinary legacy access."""
import math

from old_otjh.service import ServiceError
from . import canonical_learning as canonical


def courses_for(owner):
    rows = canonical.query('''SELECT c.id,c.source_course_ref,c.source_course_title,c.curriculum_module_ref,
        m.source_payload->'demo_progress' AS demo_progress
        FROM "Learner".learner_source_course_memberships m
        JOIN curriculum.source_courses c ON c.id=m.source_course_id AND c.deleted_at IS NULL
        JOIN login."Login_accounts" a ON a.id::text=m.source_payload #>> '{demo_progress,created_by}'
          AND a."Role"='admin' AND a."Is_active"=true
        WHERE m.learner_id=%s AND m.enrolment_id=%s AND m.deleted_at IS NULL
          AND c.source_system='old_lms' AND c.mapping_status='matched'
          AND c.mapping_method='verified_source_replacement'
          AND c.source_payload #>> '{verified_replacement,module_id}'=c.curriculum_module_ref
          AND m.source_payload #>> '{demo_progress,kind}'='owner_requested_simulation'
          AND m.source_payload #>> '{demo_progress,enrolment_id}'=m.enrolment_id::text
          AND m.source_payload #>> '{demo_progress,course_ref}'=c.source_course_ref
          AND m.source_payload #>> '{demo_progress,module_id}'=c.curriculum_module_ref
        ORDER BY c.id''', [owner['id'], owner['enrolment_id']])
    for row in rows:
        demo = canonical.decoded(row['demo_progress'], None)
        if (not isinstance(demo, dict) or isinstance(demo.get('percent'), bool)
                or not isinstance(demo.get('percent'), (int, float)) or not math.isfinite(demo['percent'])
                or not 0 <= demo['percent'] <= 100 or demo.get('hours') != 0
                or not isinstance(demo.get('excluded_activity_refs'), list)
                or any(not isinstance(ref, str) for ref in demo['excluded_activity_refs'])):
            raise ServiceError('Demonstration progress needs review.', 'demo_progress_invalid', 409)
        row['demo_progress'] = demo
    return rows


def credit(course):
    demo = (course or {}).get('demo_progress')
    return dict(progress=demo['percent'], actualHours=0, demoProgress=True) if demo else None


def catalogue(courses, definitions):
    excluded = {(str(course['source_course_ref']), ref) for course in courses
                for ref in (course.get('demo_progress') or {}).get('excluded_activity_refs', [])}
    return [row for row in definitions if (str(row['source_course_ref']), row['source_activity_id']) not in excluded]


def require_activity(source, group_id, activity_id, kind='material'):
    """Read-only demo ownership; no fallback to another learner or source account."""
    owner = canonical.require_profile(source.pk)
    email = str(source.email or '').strip().casefold()
    if not email or email != str(owner['email'] or '').strip().casefold():
        raise LookupError('Demonstration identity does not match.')
    courses = courses_for(owner)
    course = next((row for row in courses if str(row['source_course_ref']) == str(group_id)), None)
    if not course or f'{kind}:{activity_id}' in course['demo_progress']['excluded_activity_refs']:
        raise LookupError('No demonstration activity is assigned.')
    return owner
