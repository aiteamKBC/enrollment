"""Read attendance membership from the same effective plan as My Learning."""
from types import SimpleNamespace

from django.db import connections

from .learning_plan import _effective_plan_ids, group_module_ids_bulk, request_preset_cache
from .models import LearnerProfile


def assigned_module_ids(source):
    # A saved [] and explicit selections remain authoritative. Never repair a
    # stale mirror by writing from a GET or resurrecting a cleared assignment.
    return list(dict.fromkeys(_effective_plan_ids(source, request_preset_cache(source))))


def roster_sources():
    """Fetch membership keys, not every learner's nested weeks and activities."""
    projections = []
    for column in ('Training_plan', 'Learning_plan'):
        projections.append(f'''CASE WHEN jsonb_typeof("{column}"::jsonb)='array'
            THEN (SELECT coalesce(jsonb_agg(jsonb_build_object(
                'moduleId',entry->'moduleId','assignmentMode',entry->'assignmentMode')), '[]'::jsonb)
                FROM jsonb_array_elements("{column}"::jsonb) entry
                WHERE jsonb_typeof(entry)='object')
            ELSE "{column}"::jsonb END''')
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT id,"Username","Email","Programme","Group",'''
                       + ','.join(projections) + ' FROM enrolment."Created_users"')
        return [SimpleNamespace(**dict(zip(
            ('pk', 'username', 'email', 'programme', 'group', 'training_plan', 'learning_plan'), row,
        ))) for row in cursor.fetchall()]


def module_roster(module_id):
    """Effective members with existing profiles, without provisioning anything.

    Read presets in bulk so an inherited roster does not issue one query per
    learner. Resolve profile identity by enrolment id, never by a guessed name.
    """
    if not module_id:
        return []
    sources = roster_sources()
    presets = group_module_ids_bulk([(source.programme, source.group) for source in sources])
    members = {source.pk: source for source in sources
               if module_id in _effective_plan_ids(source, presets)}
    if not members:
        return []
    profiles = LearnerProfile.objects.filter(enrolment_id__in=members).values(
        'id', 'enrolment_id', 'email', 'full_name',
    )
    return [{
        'learner_profile_id': profile['id'],
        'learner_email': str(members[profile['enrolment_id']].email or profile['email'] or '').strip().casefold(),
        'learner_name': members[profile['enrolment_id']].username or profile['full_name'] or '',
    } for profile in profiles]
