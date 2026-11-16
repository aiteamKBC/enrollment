"""Additive display metadata, independent of the gated module/assignment rollout."""
from .programme_identity import find_structure_programme
from .programme_structure import learner_published_layout, resolve_assigned_cohort


def pending_home_cohort(programme_id, reason):
    return {'status': 'pending', 'programmeId': programme_id, 'cohortId': None,
            'name': None, 'layoutVersion': None, 'reason': reason}


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
    """Only SELECTs. Respect explicit pins and the recorded assignment version.

    Never assign, backfill, write a pin, or substitute a newer publication for
    the saved cohort reference. Historical records without that reference keep
    their existing profile-based lookup.
    """
    from learner_api.models import EnrolmentUser, LearnerProfile
    if not row.selection_confirmed_at:
        return pending_home_cohort(row.selected_programme, 'programme_not_confirmed')
    profiles = list(LearnerProfile.objects.filter(enrolment_id=learner_id).only('programme_id', 'cohort_id')[:2])
    if len(profiles) > 1:
        return pending_home_cohort(row.selected_programme, 'profile_ambiguous')
    profile = profiles[0] if profiles else None
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


def cohort_label_for_board(source, fallback):
    """Read the same assignment as Step 6; never synchronize legacy text fields.

    The existing cohort-learner rollout flag gates this additive board lookup.
    Old pending learners retain their current board label and publication pins.
    """
    from django.conf import settings
    if (not getattr(settings, 'PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED', False)
            or (source.learner_type or 'apprenticeship') != 'apprenticeship'):
        return fallback
    try:
        from .models import Assessment
        row = Assessment.objects.filter(learner_id=source.pk, learner_kind='apprenticeship').only(
            'selected_programme', 'selection_confirmed_at', 'modules_configuration_version', 'review').first()
        if row is not None:
            home = read_home_cohort(row, 'apprenticeship', source.pk, row.modules_configuration_version)
            if home['status'] == 'resolved':
                return home['name']
    except Exception:
        # Display-only failure cannot trigger profile creation, assignment or
        # fallback to a newer publication. Keep the existing account response.
        pass
    return fallback
