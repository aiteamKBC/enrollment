"""Shared LMS identity adapter for saved placements and new confirmations.

No LMS writes, local backfills, publication reads, or learner-specific exceptions.
Legacy labels may bridge to a single cohort within the verified programme only.
"""
import re
import unicodedata

from enrollment_service import lms_readonly
from .programme_identity import CURRICULUM_PROGRAMME_IDS


def enabled():
    import os
    return bool(os.environ.get('LMS_READONLY_DATABASE_URL', '').strip())


def label_key(value):
    value = ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', str(value or '')).casefold()))
    # Only a whole, unqualified month/year label is an intake alias. Never strip
    # qualifiers such as a campus, group or route from a saved placement.
    match = re.fullmatch(r'(feb(?:ruary)?|jun(?:e)?|oct(?:ober)?) (\d{4})', value)
    return f'{match[1][:3]} {match[2]}' if match else value


def programme_cohorts(programme_id):
    rows = lms_readonly.get_programme_cohorts(programme_id)
    if any(row['programme_id'] != programme_id for row in rows):
        raise lms_readonly.LmsReadUnavailable('cohort_identity_mismatch')
    ids = [row['cohort_id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise lms_readonly.LmsReadUnavailable('cohort_identity_ambiguous')
    return rows


def group_key(value):
    key = label_key(value)
    match = re.fullmatch(r'(?:g|group)\s*0*(\d+)', key)
    return f'g{int(match[1])}' if match else key


def curriculum_programme_id(identifier='', name=''):
    if identifier in CURRICULUM_PROGRAMME_IDS:
        return CURRICULUM_PROGRAMME_IDS[identifier]
    if identifier in CURRICULUM_PROGRAMME_IDS.values():
        return identifier
    if identifier:
        return None
    from .programme_assignment import CATALOGUE_NAMES
    matches = [CURRICULUM_PROGRAMME_IDS[key] for key, title in CATALOGUE_NAMES.items() if label_key(title) == label_key(name)]
    return matches[0] if len(matches) == 1 else None


def placement_ids(programme_id, cohort_name, group_name=''):
    if not cohort_name:
        return '', ''
    cohort, _ = saved_cohort(programme_id, None, cohort_name)
    if cohort is None:
        return '', ''
    gid = ''
    if group_name:
        rows = cohort_groups(programme_id, cohort['cohort_id'])
        groups = {r['group_id']: r['group_name'] for r in rows if r.get('group_id')}
        matches = [key for key, name in groups.items() if group_key(name) == group_key(group_name)]
        if len(matches) == 1:
            gid = matches[0]
    return cohort['cohort_id'], gid


def cohort_groups(programme_id, cohort_id):
    try:
        rows = lms_readonly.get_cohort_groups(programme_id, cohort_id)
    except lms_readonly.LmsReadUnavailable as error:
        if error.code != 'groups_access_unavailable':
            raise
        # Keep existing module-backed groups usable until the reader receives
        # SELECT on groups. Never report a permissions failure as an empty list.
        rows = [r for r in lms_readonly.get_assigned_cohort(programme_id, cohort_id) if r.get('group_id') and r.get('group_name')]
        if not rows:
            raise error from None
    groups = {}
    for row in rows:
        if row['programme_id'] != programme_id or row['cohort_id'] != cohort_id:
            raise lms_readonly.LmsReadUnavailable('group_identity_mismatch')
        identifier, name = row['group_id'], row['group_name']
        if identifier in groups and groups[identifier]['group_name'] != name:
            raise lms_readonly.LmsReadUnavailable('group_identity_ambiguous')
        groups[identifier] = {k: row[k] for k in ('group_id', 'group_name', 'programme_id', 'cohort_id')}
    return list(groups.values())


def saved_cohort(programme_id, cohort_id, cohort_name):
    """Prefer exact IDs. Resolve a legacy local ID only through a unique label."""
    rows = programme_cohorts(programme_id)
    direct = [r for r in rows if cohort_id and r['cohort_id'] == cohort_id]
    if direct:
        return direct[0], None
    key = label_key(cohort_name)
    matches = [r for r in rows if key and label_key(r['cohort_name']) == key]
    if len(matches) == 1:
        return matches[0], None
    return None, 'lms_cohort_ambiguous' if matches else 'assigned_cohort_not_in_lms'


def future_cohort(programme_id, completed_on):
    """Use the existing intake boundaries; choose only the matching LMS intake."""
    from .programme_assignment import automatic_cohort_window
    curriculum_id = CURRICULUM_PROGRAMME_IDS.get(programme_id, programme_id)
    intake, year = automatic_cohort_window(completed_on)
    key = label_key(f'{intake} {year}')
    candidates = [r for r in programme_cohorts(curriculum_id)
                  if str(r['status']).strip().casefold() in {'active', 'planned'}
                  and label_key(r['cohort_name']) == key]
    if len(candidates) != 1:
        return None
    item = candidates[0]
    return {'cohortId': item['cohort_id'], 'name': item['cohort_name'], 'fallback': False,
            'source': 'lms', 'publicationVersion': None}
