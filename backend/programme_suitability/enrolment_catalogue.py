"""Programme Structure reads and programme/cohort-only enrolment placement.

Uses existing catalogue tables and assessment JSON. No LMS requests, schema
changes, group allocation, learning-plan population, or historical backfill.
"""
import copy
import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .catalog_models import CatalogProgramme, CatalogCohort

PROGRAMME_CODES = {
    'ME_L4': 'PROG-ME-L4', 'MM_L6': 'PROG-MM-L6',
    'APM_L4': 'PROG-APM-L4', 'PCP_L6': 'PROG-PCP-L6',
}


def enabled():
    return getattr(settings, 'ENROLMENT_PROGRAMME_SOURCE', 'legacy') == 'catalogue'


def programmes(using='default'):
    # Draft means a future offering in this catalogue. Both are selectable;
    # archived records are never offered for a new assignment.
    return CatalogProgramme.objects.using(using).filter(
        archived_at__isnull=True, status__in=('draft', 'active'))


def programme_for(definition, using='default'):
    """Resolve one stable assessment identity to one local catalogue record."""
    rows = list(programmes(using).filter(level=definition['level']))
    identifier = str(definition.get('catalogueId') or '')
    exact = [row for row in rows if identifier and identifier in (str(row.pk), row.code)]
    if not exact:
        code = PROGRAMME_CODES.get(definition['id'])
        exact = [row for row in rows if row.code.casefold() == str(code).casefold()]
    if not exact:
        from .programme_assignment import CATALOGUE_NAMES
        names = {str(definition.get('title') or '').strip().casefold(),
                 CATALOGUE_NAMES.get(definition['id'], '').casefold()}
        names.add(f"{definition.get('title', '')} Level {definition['level']}".strip().casefold())
        exact = [row for row in rows if row.name.strip().casefold() in names]
    return exact[0] if len(exact) == 1 else None


def assessment_configuration(configuration):
    """Keep scoring/questions and history; present current catalogue identities."""
    result = copy.deepcopy(configuration)
    for definition in result['programmes']:
        programme = programme_for(definition)
        definition['catalogueAvailable'] = programme is not None
        if programme is not None:
            definition.update(
                catalogueId=str(programme.pk), catalogueName=programme.name,
                title=re.sub(r'\s*[—–-]?\s*Level\s+\d+\s*$', '', programme.name, flags=re.I).strip(),
                description=programme.description, summary=programme.description,
            )
    return result


def intake(completed_on):
    """Inclusive cutoffs: Feb 16–Jun 15, Jun 16–Oct 15, Oct 16–Feb 15."""
    day = (completed_on.month, completed_on.day)
    if (2, 16) <= day <= (6, 15):
        return 'June', completed_on.year
    if (6, 16) <= day <= (10, 15):
        return 'Oct', completed_on.year
    return 'Feb', completed_on.year + (day >= (10, 16))


def cohort_for(programme, completed_on, using='default'):
    label, year = intake(completed_on)
    aliases = {'Feb': ('feb', 'february'), 'June': ('jun', 'june'), 'Oct': ('oct', 'october')}[label]
    matches = []
    for cohort in CatalogCohort.objects.using(using).filter(programme=programme, archived_at__isnull=True):
        # Intake labels govern placement; a training start date is not an exam
        # cutoff and need not equal the month in the cohort's name.
        match = re.fullmatch(r'\s*(February|Feb|June|Jun|October|Oct)[\s_-]+([0-9]{4})\s*', cohort.name, re.I)
        if match and match[1].casefold() in aliases and int(match[2]) == year:
            matches.append(cohort)
    return (matches[0] if len(matches) == 1 else None), f'{label} {year}'


def programme_modules(row):
    identifier, configuration = row.selected_programme, row.configuration
    definition = next((p for p in configuration['programmes'] if p['id'] == identifier), None)
    programme = programme_for(definition) if definition else None
    if programme is None:
        from .configuration import ConfigurationError
        raise ConfigurationError('The selected programme is unavailable in Programme Structure. Please contact admissions.')
    from learner_api.models import EnrolmentUser
    placement = EnrolmentUser.all_learners.filter(pk=row.learner_id).values('programme', 'cohort').first()
    cohort_name = None
    if placement and str(placement['programme'] or '').strip().casefold() == programme.name.strip().casefold():
        # Display the current saved placement, not a calculated intake or an
        # older confirmation audit that could predate an Admin edit/clear.
        cohort_name = str(placement['cohort'] or '').strip() or None
    modules = [{
        'id': str(module.pk), 'title': module.name, 'description': '',
        'sessions': module.lectures, 'weeks': module.weeks, 'order': index,
        'active': True, 'selectable': False, 'isMandatory': False,
    } for index, module in enumerate(programme.curriculum_modules.filter(archived_at__isnull=True).order_by('name', 'id'))]
    from .modules_service import published_modules, saved_choices
    # Keep the existing academic snapshot for Skills Radar and historical PCP
    # choices. Only the Step 6 display consumes the new catalogueProgramme.
    if row.modules_configuration is not None:
        version, academic = row.modules_configuration_version, row.modules_configuration
    else:
        version, academic = published_modules()
    return {'source': 'programme_catalogue', 'configurationVersion': version,
            'assignedCohortName': cohort_name,
            'selectedProgramme': identifier, 'selectionComplete': True,
            'savedSelection': saved_choices(row.learner_kind, row.learner_id, assessment=row),
            'selectionRevision': row.modules_revision, 'configuration': academic,
            'catalogueProgramme': {
                'id': identifier, 'title': programme.name, 'modules': modules, 'pathways': [],
            }}


def _saved_cohort(programme, source, profile, using):
    values = [value.strip() for value in (source.cohort, getattr(profile, 'cohort', '')) if value and value.strip()]
    if not values and not getattr(profile, 'cohort_id', None):
        return None
    cohorts = list(CatalogCohort.objects.using(using).filter(programme=programme, archived_at__isnull=True))
    matches = [c for c in cohorts if (values and all(c.name.strip().casefold() == v.casefold() for v in values))
               or (not values and str(c.pk) == str(profile.cohort_id))]
    if len(matches) != 1:
        from .programme_assignment import ProgrammeAssignmentError
        raise ProgrammeAssignmentError('The existing cohort needs admissions review against Programme Structure. It has not been overwritten.')
    return matches[0]


def sync(row, actor='system', reconcile=False, dry_run=False):
    from learner_api.models import EnrolmentUser
    from .programme_assignment import ProgrammeAssignmentError, assignment_database, _existing_profile
    if not row.confirmed_at or not row.selection_confirmed_at:
        raise ProgrammeAssignmentError('Confirm the programme selection before assigning it to the learner.')
    previous = (row.review or {}).get('programmeAssignment')
    if previous and previous.get('status') == 'APPLIED':
        return previous
    if not row.selected_programme:
        return {'status': 'NOT_APPLICABLE'}
    using = assignment_database(row)
    from django.db import connections
    if not connections[using].in_atomic_block:
        raise ProgrammeAssignmentError('Programme synchronization requires the confirmation transaction.')
    definition = next((p for p in row.configuration['programmes'] if p['id'] == row.selected_programme), None)
    programme = programme_for(definition, using) if definition else None
    if programme is None:
        raise ProgrammeAssignmentError('The selected programme is missing or ambiguous in Programme Structure. Please contact admissions.')
    from .selection import validate_selection
    validate_selection(row.configuration, row.pathway, row.result, row.selected_programme)
    source = EnrolmentUser.all_learners.using(using).select_for_update().filter(pk=row.learner_id).first()
    if source is None or (source.learner_type or 'apprenticeship') != row.learner_kind:
        raise ProgrammeAssignmentError('The confirmed assessment no longer matches its learner account.')
    profile = _existing_profile(source, using)
    old_name = str(source.programme or '').strip()
    programme_changed = old_name.casefold() != programme.name.strip().casefold()
    profile_changed = profile and profile.programme and profile.programme.strip().casefold() != programme.name.strip().casefold()
    finalized = (str(source.onboarding_status or '').casefold() in {'submitted', 'completed'}
                 or str(source.programme_status or '').casefold() not in {'', 'fresh user', 'onboarding'}
                 or (profile and (str(profile.lifecycle_status).casefold() == 'active'
                                  or str(profile.programme_status).casefold() in {'active', 'delivery', 'ready to enrol'}))
                 or row.review_status == 'REVIEW_COMPLETED')
    if ((programme_changed or profile_changed) and finalized
            or reconcile and (programme_changed and old_name or profile_changed)):
        raise ProgrammeAssignmentError('An existing or finalised programme assignment needs admissions review; it has not been overwritten.')
    cohort = _saved_cohort(programme, source, profile, using)
    # Assessment submission is the exam completion timestamp. Confirming a
    # preference later must not move the learner into a different intake.
    completed_on = row.confirmed_at.astimezone(ZoneInfo('Europe/London')).date()
    target = None
    if cohort is None and not reconcile:
        cohort, target = cohort_for(programme, completed_on, using)
    outcome = {'status': 'WOULD_APPLY' if dry_run else 'APPLIED', 'source': 'programme_catalogue',
               'programmeId': str(programme.pk), 'programmeName': programme.name,
               'previousProgramme': old_name, 'profileId': profile.pk if profile else None,
               'automaticCohort': [str(cohort.pk), cohort.name, False] if cohort else {
                   'pending': True, 'target': target, 'reason': 'catalogue_cohort_missing_or_ambiguous'},
               'at': timezone.now().isoformat(), 'actor': actor}
    if cohort:
        outcome.update(cohortId=str(cohort.pk), cohortName=cohort.name, cohortSource='programme_catalogue')
    if dry_run:
        return outcome
    changes = []
    if source.programme != programme.name:
        source.programme = programme.name; changes.append('programme')
    if cohort and not source.cohort:
        source.cohort = cohort.name; changes.append('cohort')
    if changes:
        source.save(using=using, update_fields=changes)
    if profile:
        changes = []
        values = {'programme': programme.name, 'programme_id': str(programme.pk), 'enrolment_id': source.pk}
        if cohort:
            values.update(cohort=cohort.name, cohort_id=str(cohort.pk))
        for field, value in values.items():
            if getattr(profile, field) != value:
                setattr(profile, field, value); changes.append(field)
        if changes:
            profile.save(using=using, update_fields=[*changes, 'updated_at'])
    row.review = {**(row.review or {}), 'programmeAssignment': outcome}
    row.history = [*row.history, {'action': 'PROGRAMME_ASSIGNMENT_APPLIED', **outcome}]
    row.save(using=using, update_fields=['review', 'history'])
    return outcome
