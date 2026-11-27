"""Read-only learner schedules from an explicitly published local catalogue.

No LMS adapters, placement, allocation or learning-plan services are used here.
The catalogue is frozen only by the existing explicit Wizard publish action.
"""
import logging
from datetime import date
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone

from enrolment_api.auth import enrolment_login_required
from learner_api.models import EnrolmentUser, LearnerProfile
from .catalog_models import CatalogProgramme, CatalogCohort, CatalogModule, CatalogDelivery
from .enrolment_catalogue import PROGRAMME_CODES
from .models import Assessment

KEY = 'yourModulesCatalogue'
logger = logging.getLogger(__name__)


def enabled(layout):
    return any(not step.get('hidden') and any(
        item.get('key') == 'block.yourModules' and not item.get('hidden')
        for item in step.get('items', [])) for step in (layout or {}).get('steps', []))


def capture_catalogue():
    """Server-owned publication data; never accept a client-provided snapshot."""
    programmes = list(CatalogProgramme.objects.filter(
        archived_at__isnull=True, status__in=('draft', 'active')).order_by('id'))
    ids = [p.pk for p in programmes]
    modules = list(CatalogModule.objects.filter(programme_id__in=ids, archived_at__isnull=True)
                   .prefetch_related('revisions').order_by('name', 'id'))
    cohorts = list(CatalogCohort.objects.filter(programme_id__in=ids, archived_at__isnull=True).order_by('id'))
    deliveries = CatalogDelivery.objects.filter(
        archived_at__isnull=True, module_id__in=[m.pk for m in modules],
        group__archived_at__isnull=True, group__cohort_id__in=[c.pk for c in cohorts],
    ).select_related('module', 'module_revision', 'group__cohort').order_by('start_date', 'id')
    offerings = []
    for d in deliveries:
        if d.module.programme_id != d.group.cohort.programme_id or d.module_revision.module_id != d.module_id:
            raise ValueError('Programme Structure contains an inconsistent module delivery. Correct it before publishing Your Modules.')
        offerings.append(dict(id=str(d.pk), programmeId=str(d.module.programme_id),
            moduleId=str(d.module_id), revisionId=str(d.module_revision_id),
            cohortId=str(d.group.cohort_id), groupId=str(d.group_id), groupName=d.group.name,
            start=d.start_date.isoformat(), end=d.end_date.isoformat()))
    return dict(format=1, publishedAt=timezone.now().isoformat(), programmes=[dict(
        id=str(p.pk), code=p.code, name=p.name, externalId=p.external_id,
        modules=[dict(id=str(m.pk), name=m.name, weeks=m.weeks,
                      revisionIds=[str(r.pk) for r in m.revisions.all()])
                 for m in modules if m.programme_id == p.pk],
        cohorts=[dict(id=str(c.pk), name=c.name, externalId=c.external_id,
                      start=c.start_date.isoformat(), end=c.effective_training_end_date.isoformat())
                 for c in cohorts if c.programme_id == p.pk],
        offerings=[o for o in offerings if o['programmeId'] == str(p.pk)],
    ) for p in programmes])


def unavailable(message, version=None):
    return dict(status='unavailable', message=message, version=version, modules=[])


def published_layout(kind, learner_id):
    from enrolment_api.wizard_layout import CONN, effective_layout
    with connections[CONN].cursor() as cursor:
        current = effective_layout(cursor, kind, learner_id)
    return (current[0], current[1]) if current else (None, None)


def learner_data(kind, learner_id, today=None):
    row = Assessment.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    if not row or not row.selection_confirmed_at or not row.selected_programme:
        return unavailable('Confirm your programme in Programme Suitability before viewing Your Modules.')
    version, layout = published_layout(kind, learner_id)
    if not enabled(layout) or not isinstance((layout or {}).get(KEY), dict):
        return unavailable('Your Modules is not available in this saved wizard version. Admissions must publish and assign a compatible version.', version)
    snapshot = layout[KEY]
    if snapshot.get('format') != 1:
        return unavailable('The published Programme Structure snapshot is unavailable. Please contact admissions.', version)
    # Assessment recommendation and mutable live catalogue names never determine identity.
    definition = next((p for p in row.configuration.get('programmes', []) if p['id'] == row.selected_programme), {})
    explicit = str(definition.get('catalogueId') or row.selected_programme)
    code = PROGRAMME_CODES.get(row.selected_programme)
    matches = [p for p in snapshot['programmes'] if explicit in (p['id'], p['code'])]
    if not matches:
        matches = [p for p in snapshot['programmes'] if p['code'] == code]
    if len(matches) != 1:
        return unavailable('Your confirmed programme is unavailable in the published Programme Structure. Please contact admissions.', version)
    programme = matches[0]
    learner = EnrolmentUser.all_learners.filter(pk=learner_id).values('programme', 'cohort').first()
    if not learner or not str(learner['cohort'] or '').strip():
        return unavailable('Your permanent home cohort has not been saved. Please contact admissions.', version)
    # Current saved placement wins over any older assignment audit. Exact scoped
    # labels are supported for legacy records, never interpreted as dates.
    profiles = list(LearnerProfile.objects.filter(enrolment_id=learner_id).only('programme_id', 'cohort_id'))
    profile = profiles[0] if len(profiles) == 1 else None
    if (profile and profile.programme_id and str(profile.programme_id) not in
            (programme['id'], programme.get('externalId'))):
        return unavailable('Your saved placement differs from your confirmed programme. Please contact admissions.', version)
    if (not profile or not profile.programme_id) and str(learner['programme'] or '').strip() != programme['name']:
        return unavailable('Your saved placement differs from your confirmed programme. Please contact admissions.', version)
    cohort_name = str(learner['cohort']).strip()
    candidates = [c for c in programme['cohorts'] if c['name'] == cohort_name]
    if profile and str(profile.programme_id) in (programme['id'], programme.get('externalId')):
        by_id = [c for c in candidates if str(profile.cohort_id) in (c['id'], c.get('externalId'))]
        if profile.cohort_id:
            candidates = by_id
    if len(candidates) != 1:
        return unavailable('Your saved home cohort cannot be verified in the published Programme Structure. Please contact admissions.', version)
    home = candidates[0]
    try:
        start, end = date.fromisoformat(home['start']), date.fromisoformat(home['end'])
        if end < start:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        return unavailable('Your published home cohort has incomplete training dates. Please contact admissions.', version)
    today = today or timezone.localdate(timezone.now(), ZoneInfo('Europe/London'))
    cohort_by_id = {c['id']: c for c in programme['cohorts']}
    module_ids = {m['id'] for m in programme['modules']}
    # A publication freezes academic content and dates, not permission to offer
    # a delivery that has since been withdrawn. This only removes unavailable
    # identities; it never imports unpublished dates, names or new schedules.
    available = {str(pk): (str(module), str(group), str(cohort), str(pid))
        for pk, module, group, cohort, pid in CatalogDelivery.objects.filter(
            id__in=[o['id'] for o in programme['offerings']], archived_at__isnull=True,
            module__programme_id=programme['id'], group__cohort__programme_id=programme['id'],
            module__archived_at__isnull=True, module__programme__archived_at__isnull=True,
            module__programme__status__in=('draft', 'active'), group__archived_at__isnull=True,
            group__cohort__archived_at__isnull=True,
        ).values_list('id', 'module_id', 'group_id', 'group__cohort_id', 'module__programme_id')}
    offerings = []
    for offering in programme['offerings']:
        if offering['programmeId'] != programme['id'] or offering['moduleId'] not in module_ids:
            continue
        if available.get(offering['id']) != (offering['moduleId'], offering['groupId'], offering['cohortId'], programme['id']):
            continue
        cohort = cohort_by_id.get(offering['cohortId'])
        if not cohort:
            continue
        begins, finishes = date.fromisoformat(offering['start']), date.fromisoformat(offering['end'])
        if finishes < begins:
            continue
        is_home = cohort['id'] == home['id']
        # Date-only schedules have no exact teaching start time: today is the
        # earliest eligible date. Never infer a time from a group weekday.
        if not is_home and not (start <= begins <= end and begins >= today):
            continue
        offerings.append({**offering, 'cohortName': cohort['name'], 'cohortStart': cohort['start'], 'home': is_home,
                          'extendsWindow': finishes > end})
    return dict(status='ready', version=version, source='published-programme-structure',
        selectedProgramme=row.selected_programme, programmeId=programme['id'], programmeName=programme['name'],
        homeCohort=home, evaluatedOn=today.isoformat(), modules=programme['modules'], offerings=offerings,
        # A saved PCP selection must be scoped using the existing academic IDs
        # and verified revision bridge on the client. Missing data is not all electives.
        requiresModuleSelection=row.selected_programme == 'PCP_L6')


@enrolment_login_required
def your_modules(request, kind, learner_id):
    from .views import learner_record
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    try:
        if learner_record(kind, learner_id) is None:
            return JsonResponse({'error': 'Not found.'}, status=404)
        data = learner_data(kind, learner_id)
    except LookupError:
        data = unavailable('Your saved wizard publication is unavailable. Please contact admissions.')
    except (DatabaseError, ValueError, KeyError, TypeError):
        logger.exception('Your Modules read failed learner_id=%s kind=%s', learner_id, kind)
        return JsonResponse({'error': 'Could not load Your Modules. Please retry.'}, status=503)
    response = JsonResponse(data)
    response['Cache-Control'] = 'private, no-store'
    return response
