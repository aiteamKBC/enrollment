"""Read-only completion lineage for verified course successors.

The current catalogue owns content and placement. The canonical student-activity
record owns previous completion. Neither attempts, attendance nor hours are
created by this projection.
"""
import json
from collections import Counter

def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def project_replacement_learning(components, settings_by_id, courses, activities):
    """Match exact course + material/quiz identity, never names or positions."""
    catalogue = {}
    for activity in activities:
        key = (f"legacy:{activity['group_id']}", activity.get('catalogue_kind'),
               str(activity.get('source_activity_id')))
        catalogue.setdefault(key, []).append(activity)
    candidates = {}
    course_uses = Counter(courses.values())
    for component in components:
        ident = component.get('componentId')
        course = courses.get(f"current:{component.get('moduleId')}")
        if not ident or not course or course_uses[course] != 1 or component.get('retired'):
            continue
        settings = _object(settings_by_id.get(ident))
        legacy = _object(settings.get('legacySettings'))
        source_id = str(legacy.get('sourceLessonId') or '')
        if not source_id.isdigit() or int(source_id) <= 0:
            continue
        kind = 'quiz' if component.get('isQuiz') or component.get('type') == 'quiz' else 'material'
        # sourceMaterialKey disambiguates imported quizzes from companion
        # readings. Contradictory provenance needs review, not title matching.
        material_key = str(legacy.get('sourceMaterialKey') or '')
        if material_key.startswith('quiz-') and (kind != 'quiz' or material_key != f'quiz-{source_id}'):
            continue
        key = (course, kind, str(int(source_id)))
        if len(catalogue.get(key, [])) == 1:
            candidates[ident] = key
    uses = Counter(candidates.values())
    result = []
    for component in components:
        key = candidates.get(component.get('componentId'))
        if key is None or uses[key] != 1:
            result.append(component)
            continue
        activity = catalogue[key][0]
        result.append({**component, 'previousLearning': {
            'courseRef': key[0], 'activityId': activity['activity_id'],
            'sourceActivityId': int(key[2]), 'catalogueKind': key[1],
            'completed': activity.get('completed') is True,
        }})
    return result


def annotate_replacement_learning(components, enrolment_id):
    """Expose only the server-owned, approved and frozen component matches."""
    from django.db import connections
    from .progress_transfer_store import read_state
    if not components:
        return []
    items = read_state(enrolment_id)['items']
    modules = {item['moduleId'] for item in items if item['status'] == 'approved' and item.get('matchedComponents')}
    ids = [c['componentId'] for c in components if c.get('componentId') and c.get('moduleId') in modules]
    settings = {}
    if ids:
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('''SELECT id,jsonb_build_object('legacySettings',settings_json->'legacySettings')
                FROM curriculum.components WHERE id=ANY(%s) AND module_catalogue_id=ANY(%s)''',
                [ids, sorted(modules)])
            settings = dict(cursor.fetchall())
    return project_approved_learning(components, settings, items)


def project_approved_learning(components, settings_by_id, items):
    """Revalidate lineage without reading mutable old results or awarding hours."""
    clean = [{key: value for key, value in c.items() if key != 'previousLearning'} for c in components]
    approved = [item for item in items if item.get('status') == 'approved']
    courses = {f"current:{item['moduleId']}": item['courseRef'] for item in approved}
    activities, matches = {}, {}
    for item in approved:
        for ident, match in item.get('matchedComponents', {}).items():
            if match.get('completed') is not True or match.get('courseRef') != item['courseRef']:
                continue
            matches[(item['moduleId'], ident)] = match
            activities[match['activityId']] = {
                'group_id': int(item['courseRef'][7:]), 'catalogue_kind': match['catalogueKind'],
                'source_activity_id': match['sourceActivityId'], 'activity_id': match['activityId'], 'completed': True}
    projected = project_replacement_learning(clean, settings_by_id, courses, list(activities.values()))
    result = [c if c.get('previousLearning') == matches.get((c.get('moduleId'), c.get('componentId')))
              else {key: value for key, value in c.items() if key != 'previousLearning'} for c in projected]
    # Manual equivalence is an explicit admin decision saved on this learner's
    # approved request. It does not rewrite shared catalogue provenance.
    from .progress_transfer_matching import target_fingerprint
    manual = {}
    for item in approved:
        source_uses = Counter(match.get('activityId') for match in item.get('matchedComponents', {}).values())
        for ident, match in item.get('matchedComponents', {}).items():
            ref = f"{match.get('catalogueKind')}:{match.get('sourceActivityId')}"
            if (match.get('manualMatch') is True and match.get('completed') is True
                    and match.get('courseRef') == item['courseRef']
                    and ref in item.get('completedSourceActivities', [])
                    and match.get('activityId') == f"catalogue:{item['courseRef'][7:]}:{ref}"
                    and source_uses[match['activityId']] == 1):
                manual[(item['moduleId'], ident)] = match
    for component in result:
        match = manual.get((component.get('moduleId'), component.get('componentId')))
        if (match and not component.get('retired') and match.get('targetFingerprint') ==
                target_fingerprint(component, settings_by_id.get(component.get('componentId')))):
            component['previousLearning'] = match
    return result
