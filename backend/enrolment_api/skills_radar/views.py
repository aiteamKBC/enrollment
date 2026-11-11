"""Programme-scoped CSV previews and drafts; publishing remains the builder's job."""
import json
import logging

from django.db import DatabaseError, transaction
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect

from enrolment_api.auth import enrolment_login_required
from enrolment_api.models import ExtendedIlr
from programme_suitability.models import ModuleConfigurationDraft
from programme_suitability.modules_configuration import defaults
from programme_suitability.permissions import is_staff

from . import service
from .catalogue import APPROVALS, KEY, PROGRAMMES, CatalogueError, check_approval, compare, current_catalogues, validate
from .csv_io import MAX_BYTES, export_csv, parse_csv

logger = logging.getLogger(__name__)
CSV_TYPES = {'text/csv', 'application/csv', 'application/vnd.ms-excel', 'text/plain', 'application/octet-stream'}


def error(exc, status=400):
    return JsonResponse({'error': str(exc), 'errors': getattr(exc, 'errors', [])}, status=status)


def actor(request):
    account = getattr(request, 'login_account', None)
    return f'skills-radar:platform:{account.pk}' if account else f'skills-radar:django:{request.user.pk}'


@enrolment_login_required
@csrf_protect
def catalogue_admin(request, programme=None):
    if not is_staff(request):
        return error('Admissions staff access required.', 403)
    if programme is not None and programme not in PROGRAMMES:
        return error('Unknown programme.', 404)
    if request.method not in {'GET', 'POST'} or (request.method == 'POST' and programme is None):
        return error('Method not allowed.', 405)
    try:
        base, layout = service.published()
        catalogues = current_catalogues(layout)
        if request.method == 'GET':
            if programme is None:
                return JsonResponse({'catalogues': catalogues, 'baseVersion': base, 'modules': layout.get('programmeModules') or defaults()})
            response = HttpResponse(export_csv(catalogues[programme], request.GET.get('template') == '1'), content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="{programme}-skills-radar{"-template" if request.GET.get("template") == "1" else ""}.csv"'
            response['Cache-Control'] = 'no-store'
            return response
        upload = request.FILES.get('file')
        if not upload or len(request.FILES) != 1:
            raise CatalogueError('Upload one complete CSV in the file field.')
        if upload.size > MAX_BYTES:
            return error('CSV exceeds the 1 MB limit.', 413)
        if not upload.name.lower().endswith('.csv') or upload.content_type not in CSV_TYPES:
            raise CatalogueError('Upload a .csv file with a CSV or plain-text content type.')
        candidate = parse_csv(upload.read(MAX_BYTES + 1), programme, layout.get('programmeModules') or defaults(), catalogues[programme])
        logger.info('Skills Radar CSV validated actor=%s programme=%s version=%s count=%s', actor(request), programme, candidate['version'], len(candidate['records']))
        return JsonResponse({'catalogue': candidate, 'comparison': compare(catalogues[programme], candidate), 'baseVersion': base})
    except CatalogueError as exc:
        return error(exc)
    except DatabaseError:
        return error('Could not load Skills Radar configuration. Please retry.', 503)


@enrolment_login_required
@csrf_protect
def catalogue_draft(request):
    if not is_staff(request):
        return error('Admissions staff access required.', 403)
    if request.method not in {'GET', 'PUT', 'DELETE'}:
        return error('Method not allowed.', 405)
    try:
        key = actor(request)
        if request.method == 'GET':
            row = ModuleConfigurationDraft.objects.filter(pk=key).first()
            return JsonResponse({'configuration': row.configuration if row else None, 'baseVersion': row.base_version if row else None})
        if request.method == 'DELETE':
            ModuleConfigurationDraft.objects.filter(pk=key).delete()
            return JsonResponse({'deleted': True})
        if len(request.body) > 4 * MAX_BYTES:
            return error('Draft is too large.', 413)
        try:
            data = json.loads(request.body)
        except ValueError as exc:
            raise CatalogueError('Provide a valid JSON draft.') from exc
        if not isinstance(data, dict):
            raise CatalogueError('Provide a JSON object.')
        # Share the publisher's table lock so the base version cannot change
        # between validation and draft persistence. This never writes a layout.
        from enrolment_api.wizard_layout import CONN, LAYOUT_TABLE, read_current, _table_exists
        with transaction.atomic(using=CONN):
            with service.connections[CONN].cursor() as cursor:
                if _table_exists(cursor):
                    cursor.execute(f'LOCK TABLE enrolment."{LAYOUT_TABLE}" IN SHARE MODE')
                current = read_current(cursor)
            base, layout = (current[0], current[1]) if current else (None, {})
            if type(data.get('baseVersion')) not in (int, type(None)) or data.get('baseVersion') != base:
                return error('The wizard was published elsewhere. Reload before saving this draft.', 409)
            old = current_catalogues(layout)
            configuration = data.get('configuration')
            if not isinstance(configuration, dict) or not isinstance(configuration.get(KEY), dict) or set(configuration[KEY]) != set(PROGRAMMES):
                raise CatalogueError('Provide all four programme catalogues.')
            approvals = configuration.get(APPROVALS) or {}
            if not isinstance(approvals, dict):
                raise CatalogueError('Invalid import confirmations.')
            cleaned, confirmations = {}, {}
            for pid in PROGRAMMES:
                cleaned[pid] = validate(pid, configuration[KEY][pid], layout.get('programmeModules') or defaults(), old[pid])
                difference = check_approval(old[pid], cleaned[pid], approvals.get(pid))
                if cleaned[pid]['version'] != old[pid]['version']:
                    confirmations[pid] = {'againstVersion': old[pid]['version'], 'completeSnapshot': True,
                                          'removedCodes': difference['removedCodes'], 'categoryCodes': difference['categoryCodes']}
            result = {KEY: cleaned, APPROVALS: confirmations}
            row = ModuleConfigurationDraft.objects.filter(pk=key).first()
            if (row or confirmations) and (not row or row.base_version != base or row.configuration != result):
                ModuleConfigurationDraft.objects.update_or_create(pk=key, defaults={'base_version': base, 'configuration': result})
                logger.info('Skills Radar draft saved actor=%s base_version=%s', key, base)
        return JsonResponse({'configuration': result, 'baseVersion': base})
    except CatalogueError as exc:
        return error(exc)
    except DatabaseError:
        return error('Could not save or load Skills Radar draft. Please retry.', 503)


@enrolment_login_required
def learner_catalogue(request, kind, learner_id):
    if request.method != 'GET':
        return error('Method not allowed.', 405)
    from enrolment_api.extended_ilr import KINDS
    model = KINDS.get(kind)
    if model is None:
        return error('Unknown learner kind.', 400)
    try:
        learner = model.objects.filter(pk=learner_id).first()
        if learner is None:
            return error('Not found.', 404)
        row = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).first()
        # Also recognise legacy per-step data when no JSON draft was written.
        stored = row.wizard_draft if row else None
        if not stored or 'skillsRadar' not in stored:
            radar = service.read_legacy_radar(kind, learner_id)
            stored = {**(stored or {}), **({'skillsRadar': radar} if radar is not None else {})}
        radar = (stored or {}).get('skillsRadar') or {}
        _, layout = service.published()
        profile = None
        layout_source = None
        if getattr(settings, 'PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED', False):
            from learner_api.models import LearnerProfile
            from programme_suitability.models import Assessment
            from programme_suitability.programme_structure import learner_published_layout

            profile = LearnerProfile.objects.filter(enrolment_id=learner_id).only('programme_id', 'cohort_id').first()
            module_version = Assessment.objects.filter(learner_id=learner_id).values_list(
                'modules_configuration_version', flat=True).first()
            _, pinned_layout, layout_source = learner_published_layout(kind, learner_id, module_version=module_version)
            if pinned_layout is not None:
                layout = pinned_layout
        payload = service.learner_payload(kind, learner, radar, layout)
        if (getattr(settings, 'PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED', False)
                and layout_source != 'legacy' and payload.get('programmeId')):
            from programme_suitability.programme_structure import resolve_published, resolve_assigned_cohort
            if profile and profile.programme_id == payload['programmeId'] and profile.cohort_id and layout:
                resolved = resolve_published(layout, compare_legacy=False)
                cohort = resolve_assigned_cohort(resolved['snapshot'], profile.programme_id, profile.cohort_id)
                if resolved['source'] == 'canonical' and cohort is None:
                    return error('The assigned Cohort is unavailable in this learner\'s saved published layout. Admissions must review the assignment.', 409)
                programme = next((p for p in resolved['snapshot'].get('programmes', []) if p.get('id') == payload['programmeId']), None)
                # A catalogue pinned independently of the layout may predate
                # cohort coverage. Never graft newer mappings onto its records.
                from .catalogue import current_catalogues
                compatible_catalogue = (
                    payload.get('catalogue', {}).get('version') ==
                    current_catalogues(layout)[payload['programmeId']]['version']
                )
                if cohort and programme and payload.get('catalogue') and compatible_catalogue:
                    names = {m.get('id'): m.get('title') for m in cohort.get('modules', [])}
                    by_ksb = {}
                    for rel in cohort.get('mappings', []):
                        if rel.get('moduleId') in names:
                            by_ksb.setdefault(rel.get('ksbId'), []).append(names[rel['moduleId']])
                    for record in payload['catalogue'].get('records', []):
                        record['moduleNames'] = sorted(set(by_ksb.get(record.get('id'), [])))
                        module_ids = [rel.get('moduleId') for rel in cohort.get('mappings', []) if rel.get('ksbId') == record.get('id') and rel.get('moduleId') in names]
                        record['moduleKeys'] = sorted(set(module_ids))
                        record.pop('moduleKey', None)
        if getattr(settings, 'PROGRAMME_STRUCTURE_SHADOW_ENABLED', False) and payload.get('programmeId'):
            from programme_suitability.shadow_runtime import compare
            from programme_suitability.shadow_runtime import saved_module_ids
            selected = saved_module_ids(learner_id)
            compare(kind, learner_id, payload['programmeId'], selected, module_version=None, catalogue_version=(radar.get('catalogueVersions') or {}).get(payload['programmeId']))
        return JsonResponse(payload)
    except CatalogueError as exc:
        return error(exc, 409)
    except LookupError as exc:
        return error(exc, 409)
    except DatabaseError:
        return error('Could not load the saved Skills Radar version. Please retry.', 503)
