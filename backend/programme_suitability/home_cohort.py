"""Additive display metadata, independent of the gated module/assignment rollout."""
import os

from .programme_identity import CURRICULUM_PROGRAMME_IDS, find_structure_programme
from .programme_structure import learner_published_layout, resolve_assigned_cohort


def pending_home_cohort(programme_id, reason):
    return {'status': 'pending', 'programmeId': programme_id, 'cohortId': None,
            'name': None, 'layoutVersion': None, 'reason': reason}


def resolve_lms_home(programme_id, persisted_programme_id, cohort_id, group_id=None, *, cohort_name=None, group_name=None):
    """Live display projection; never change assignments or academic snapshots."""
    from enrollment_service import lms_readonly
    curriculum_id = CURRICULUM_PROGRAMME_IDS.get(programme_id)
    if not curriculum_id or persisted_programme_id not in {programme_id, curriculum_id}:
        return pending_home_cohort(programme_id, 'programme_assignment_mismatch')
    try:
        rows = lms_readonly.get_assigned_cohort(curriculum_id, cohort_id, group_id) if cohort_id else []
        if not rows and cohort_name:
            from .lms_placement import saved_cohort
            cohort, reason = saved_cohort(curriculum_id, cohort_id, cohort_name)
            if cohort is None:
                return {**pending_home_cohort(programme_id, reason), 'assignedCohortName': cohort_name}
            cohort_id = cohort['cohort_id']
            rows = lms_readonly.get_assigned_cohort(curriculum_id, cohort_id, group_id)
        if rows and group_name and (not group_id or all(r.get('module_catalogue_id') is None for r in rows)):
            from .lms_placement import group_key, cohort_groups
            groups = {r['group_id']: r['group_name'] for r in cohort_groups(curriculum_id, cohort_id)}
            matches = [gid for gid, name in groups.items() if group_key(name) == group_key(group_name)]
            if len(matches) != 1:
                return {**pending_home_cohort(programme_id, 'lms_group_ambiguous'), 'assignedCohortName': cohort_name}
            group_id = matches[0]
            group_name = groups[group_id]
            rows = lms_readonly.get_assigned_cohort(curriculum_id, cohort_id, group_id)
    except lms_readonly.LmsReadUnavailable:
        return pending_home_cohort(programme_id, 'lms_unavailable')
    if (not rows or any(r['programme_id'] != curriculum_id or r['cohort_id'] != cohort_id for r in rows)
            or len({r['cohort_name'] for r in rows}) != 1 or not (rows[0]['cohort_name'] or '').strip()):
        return pending_home_cohort(programme_id, 'assigned_cohort_unavailable')
    modules, seen = [], set()
    group_names = set()
    if group_id and group_name:
        group_names.add(group_name)
    for item in rows:
        mid = item['module_catalogue_id']
        if mid is None:
            continue
        if mid in seen or (group_id and item['group_id'] != group_id):
            return pending_home_cohort(programme_id, 'lms_assignment_ambiguous')
        seen.add(mid)
        if group_id and item.get('group_name'):
            group_names.add(item['group_name'])
        modules.append({'id': mid, 'title': item['title'], 'sessions': item['sessions_number'],
                        'weeks': item.get('weeks_number'),
                        'groupId': item.get('group_id'), 'groupName': item.get('group_name'),
                        'start': str(item['start_date']) if item['start_date'] else None,
                        'end': str(item['end_date']) if item['end_date'] else None})
    return {'status': 'resolved', 'programmeId': programme_id, 'cohortId': cohort_id,
            'name': rows[0]['cohort_name'], 'layoutVersion': None, 'reason': None, 'source': 'lms',
            'groupId': group_id, 'groupName': next(iter(group_names)) if len(group_names) == 1 else None,
            'lmsModules': modules}


def read_lms_home(row, profile, learner_id):
    """One runtime path for linked profiles, account-only and legacy learners."""
    from learner_api.models import EnrolmentUser
    from .lms_placement import label_key
    from .programme_assignment import CATALOGUE_NAMES
    if profile and profile.programme_id and (profile.cohort_id or profile.cohort):
        result = resolve_lms_home(row.selected_programme, profile.programme_id, profile.cohort_id,
                                  profile.group_id or None, cohort_name=profile.cohort, group_name=profile.group_name)
    else:
        source = EnrolmentUser.all_learners.filter(pk=learner_id).only('programme', 'cohort', 'group').first()
        assignment = (row.review or {}).get('programmeAssignment') or {}
        definition = next((p for p in (row.configuration or {}).get('programmes', [])
                           if p.get('id') == row.selected_programme), {})
        allowed = {label_key(CATALOGUE_NAMES.get(row.selected_programme)), label_key(definition.get('title'))}
        recorded = assignment.get('status') == 'APPLIED'
        if recorded and assignment.get('programmeId') in {row.selected_programme, CURRICULUM_PROGRAMME_IDS.get(row.selected_programme)}:
            allowed.add(label_key(assignment.get('programmeName')))
        if not source or not label_key(source.programme) or label_key(source.programme) not in allowed:
            result = pending_home_cohort(row.selected_programme, 'programme_assignment_mismatch')
        elif profile and profile.programme_id and profile.programme_id not in {row.selected_programme, CURRICULUM_PROGRAMME_IDS.get(row.selected_programme)}:
            result = pending_home_cohort(row.selected_programme, 'programme_assignment_mismatch')
        elif not source.cohort:
            result = pending_home_cohort(row.selected_programme, 'assignment_missing')
        else:
            # A later manual account placement takes precedence over an old audit.
            cohort_id = assignment.get('cohortId') if recorded and source.cohort == assignment.get('cohortName') else None
            result = resolve_lms_home(row.selected_programme, CURRICULUM_PROGRAMME_IDS.get(row.selected_programme),
                                      cohort_id, cohort_name=source.cohort, group_name=source.group)
    return {**result, 'source': 'lms'}


def resolve_home_cohort(programme_id, confirmed_at, persisted_programme_id,
                        cohort_id, layout, layout_version):
    """Pure lookup: persisted assignment + canonical pinned snapshot only."""
    pending = lambda reason: pending_home_cohort(programme_id, reason)
    if not confirmed_at:
        return pending('programme_not_confirmed')
    if not persisted_programme_id or not cohort_id:
        return pending('assignment_missing')
    snapshot = (layout or {}).get('programmeStructure')
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('programmes'), list):
        # Never call resolve_published(): its legacy Feb 2027 compatibility
        # projection is not evidence of a real assigned/published cohort.
        return pending('canonical_publication_missing')
    confirmed = find_structure_programme(snapshot, programme_id)
    persisted = find_structure_programme(snapshot, persisted_programme_id)
    if not confirmed or not persisted or confirmed['id'] != persisted['id']:
        return pending('programme_assignment_mismatch')
    cohort = resolve_assigned_cohort(snapshot, persisted_programme_id, cohort_id)
    if not cohort or not isinstance(cohort.get('name'), str) or not cohort['name'].strip():
        return pending('assigned_cohort_unavailable')
    return {'status': 'resolved', 'programmeId': programme_id, 'cohortId': cohort_id,
            'name': cohort['name'], 'layoutVersion': layout_version, 'reason': None}


def read_home_cohort(row, kind, learner_id, module_version):
    """Only SELECTs. Prefer the current profile's explicit assignment IDs.

    Configured LMS reads supply live display metadata without changing academic
    snapshots. Without the reader, retain the existing pinned-publication lookup.
    Never assign, backfill, or write a pin.
    """
    from learner_api.models import EnrolmentUser, LearnerProfile
    if not row.selection_confirmed_at:
        return pending_home_cohort(row.selected_programme, 'programme_not_confirmed')
    profiles = list(LearnerProfile.objects.filter(enrolment_id=learner_id).only('programme_id', 'cohort_id', 'group_id', 'cohort', 'group_name')[:2])
    if len(profiles) > 1:
        return pending_home_cohort(row.selected_programme, 'profile_ambiguous')
    profile = profiles[0] if profiles else None
    from .lms_placement import enabled
    if enabled():
        return read_lms_home(row, profile, learner_id)
    review = getattr(row, 'review', None)
    assignment = review.get('programmeAssignment') if isinstance(review, dict) else None
    assignment = assignment if isinstance(assignment, dict) else {}
    version = assignment.get('cohortPublicationVersion')
    recorded = (assignment.get('status') == 'APPLIED' and assignment.get('cohortAssignmentVersion') == 1
                and isinstance(assignment.get('cohortId'), str) and bool(assignment['cohortId'])
                and isinstance(assignment.get('programmeId'), str) and bool(assignment['programmeId'])
                and type(version) is int and version > 0)
    use_recorded_publication = False
    if profile and profile.programme_id and profile.cohort_id:
        programme_id, cohort_id = profile.programme_id, profile.cohort_id
        # Preserve a later administrator's profile assignment over old audit data.
        use_recorded_publication = bool(recorded and cohort_id == assignment['cohortId']
                                        and programme_id == assignment['programmeId'])
    elif recorded:
        source = EnrolmentUser.all_learners.filter(pk=learner_id).only('programme', 'cohort').first()
        if (not source or source.programme != assignment.get('programmeName')
                or source.cohort != assignment.get('cohortName')
                or (profile and profile.programme_id and profile.programme_id != assignment['programmeId'])):
            return pending_home_cohort(row.selected_programme, 'account_assignment_changed')
        programme_id, cohort_id = assignment['programmeId'], assignment['cohortId']
        use_recorded_publication = True
    else:
        # Old APPLIED records and free-text cohort names never become stable
        # assignments merely by opening a page. No historical backfill here.
        return pending_home_cohort(row.selected_programme, 'assignment_missing')
    try:
        layout_version, layout, publication_source = learner_published_layout(kind, learner_id, module_version=module_version)
        if use_recorded_publication and layout_version != version:
            if publication_source != 'latest':
                return pending_home_cohort(row.selected_programme, 'cohort_publication_mismatch')
            # A new account need not have module/layout pins yet. Honor the
            # publication saved in its cohort outcome, never a newer default.
            from django.db import connections
            from enrolment_api.wizard_layout import CONN, read_version
            with connections[CONN].cursor() as cursor:
                saved = read_version(cursor, version)
            if saved is None:
                return pending_home_cohort(row.selected_programme, 'pinned_publication_unavailable')
            layout_version, layout = saved[:2]
    except LookupError:
        return pending_home_cohort(row.selected_programme, 'pinned_publication_unavailable')
    return resolve_home_cohort(row.selected_programme, row.selection_confirmed_at,
                               programme_id, cohort_id, layout, layout_version)


def home_cohort_for_board(source):
    """Read the same assignment as Step 6; never synchronize legacy text fields.

    A configured LMS reader or the existing cohort-learner flag enables lookup.
    Old pending learners retain their current board label and publication pins.
    """
    from django.conf import settings
    if (not (os.environ.get('LMS_READONLY_DATABASE_URL', '').strip()
             or getattr(settings, 'PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED', False))
            or (source.learner_type or 'apprenticeship') != 'apprenticeship'):
        return None
    try:
        from .models import Assessment
        row = Assessment.objects.filter(learner_id=source.pk, learner_kind='apprenticeship').only(
            'selected_programme', 'selection_confirmed_at', 'modules_configuration_version', 'review').first()
        if row is not None:
            home = read_home_cohort(row, 'apprenticeship', source.pk, row.modules_configuration_version)
            return home
    except Exception:
        # Display-only failure cannot trigger profile creation, assignment or
        # fallback to a newer publication. Keep the existing account response.
        pass
    return None


def cohort_label_for_board(source, fallback):
    home = home_cohort_for_board(source)
    return home['name'] if home and home['status'] == 'resolved' else fallback
