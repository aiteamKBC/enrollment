import json
from django.db import connections, transaction, router, IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from enrolment_api.auth import enrolment_login_required
from enrolment_api.skills_radar.catalogue import CatalogueError
from programme_suitability.models import ModuleConfigurationDraft
from programme_suitability.programme_structure import KEY, version, validate_structure
from programme_suitability.permissions import is_staff
from django.conf import settings

# Programme Structure is shared system configuration, not a personal builder draft.
SHARED_DRAFT_KEY = 'programme-structure:shared'

def _actor(request):
    return SHARED_DRAFT_KEY

def _error(message, status=400): return JsonResponse({'error': message}, status=status)

@enrolment_login_required
@csrf_protect
def programme_structure_validate(request):
    if not is_staff(request): return _error('Admissions staff access required.', 403)
    if request.method != 'POST': return _error('Method not allowed.', 405)
    try: data = json.loads(request.body or '{}')
    except ValueError: return _error('Request body must be valid JSON.')
    structure = data.get('structure') if isinstance(data, dict) else None
    try:
        from programme_suitability.programme_structure import validate_structure
        validate_structure(structure)
    except (ValueError, TypeError) as exc:
        return _error(str(exc), 400)
    return JsonResponse({'valid': True})

@enrolment_login_required
@csrf_protect
@enrolment_login_required
@csrf_protect
def programme_structure_preview(request):
    if not is_staff(request): return _error('Admissions staff required.', 403)
    if request.method != 'POST': return _error('Method not allowed.', 405)
    try: data = json.loads(request.body or '{}')
    except ValueError: return _error('Request body must be valid JSON.')
    structure = data.get('structure') if isinstance(data, dict) else None
    try:
        from programme_suitability.publication import PublicationBlocked, preflight
        current_version = data.get('currentVersion') if isinstance(data, dict) else None
        base_version = data.get('baseVersion', current_version) if isinstance(data, dict) else None
        from enrolment_api.wizard_layout import CONN, read_current
        from programme_suitability.programme_structure import resolve_published
        with connections[CONN].cursor() as cursor:
            current = read_current(cursor)
        published_version = current[0] if current else None
        published_layout = current[1] if current else {}
        published_structure = resolve_published(published_layout)['snapshot'] if current else None
        if base_version is not None and base_version != published_version:
            return JsonResponse({'valid': False, 'unsupportedProgrammes': [], 'version': version(structure) if isinstance(structure, dict) else None, 'publishedVersion': published_version, 'changes': [], 'historicalLearnersUnchanged': True, 'blockers': ['Published settings changed. Reconcile the shared draft before reviewing publication.']})
        try:
            result = preflight({'programmeStructure': structure}, current_version=published_version, base_version=base_version)
            from programme_suitability.publication import compare_structures, build_published_layout
            merged = build_published_layout(published_layout, result)
            return JsonResponse({'valid': True, 'unsupportedProgrammes': [], 'version': result['version'], 'publishedVersion': version(published_structure) if isinstance(published_structure, dict) else None, 'changes': compare_structures(published_structure, structure) if isinstance(published_structure, dict) else [], 'projections': {'programmeModules': merged['programmeModules'], 'skillsRadarCatalogues': merged['skillsRadarCatalogues']}, 'historicalLearnersUnchanged': True, 'publishingEnabled': bool(getattr(settings, 'PROGRAMME_STRUCTURE_PUBLISHING_ENABLED', False)), 'blockers': []})
        except PublicationBlocked as exc:
            return JsonResponse({'valid': False, 'unsupportedProgrammes': [p.get('id') for p in structure.get('programmes', []) if p.get('id') not in {'ME_L4', 'MM_L6', 'APM_L4', 'PCP_L6'}], 'version': version(structure) if isinstance(structure, dict) else None, 'historicalLearnersUnchanged': True, 'blockers': [str(exc)]})
    except (ValueError, TypeError) as exc: return _error(str(exc), 400)

@enrolment_login_required
@csrf_protect
def programme_structure_draft(request):
    if not is_staff(request):
        return _error('Admissions staff access required.', 403)
    database = router.db_for_write(ModuleConfigurationDraft) or 'default'
    if request.method == 'GET':
        row = ModuleConfigurationDraft.objects.using(database).filter(pk=SHARED_DRAFT_KEY).first()
        return JsonResponse({'structure': row.configuration if row else None,
                             'baseVersion': row.base_version if row else None,
                             'revision': (row.configuration or {}).get('_draftRevision', 0) if row else 0,
                             'scope': 'shared'})
    if request.method not in ('PUT', 'DELETE'):
        return _error('Method not allowed.', 405)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return _error('Request body must be valid JSON.')
    if not isinstance(data, dict):
        return _error('Request body must be a JSON object.')
    expected = data.get('revision')
    if type(expected) is not int or expected < 0:
        return _error('A non-negative draft revision is required.')
    structure = data.get('structure')
    base = data.get('baseVersion')
    if request.method == 'PUT':
        if base is not None and (type(base) is not int or base < 1):
            return _error('Invalid base version.')
        try:
            validate_structure(structure)
        except (ValueError, TypeError) as exc:
            return _error(str(exc), 400)
    try:
        with transaction.atomic(using=database):
            row = ModuleConfigurationDraft.objects.using(database).select_for_update().filter(pk=SHARED_DRAFT_KEY).first()
            revision = (row.configuration or {}).get('_draftRevision', 0) if row else 0
            if expected != revision:
                return _error('This shared draft changed since you loaded it. Reload before saving.', 409)
            if request.method == 'DELETE':
                if row:
                    row.delete(using=database)
                return JsonResponse({'deleted': True})
            if row and row.base_version != base:
                return _error('The shared settings changed. Reload before saving.', 409)
            updated = dict(structure)
            updated['_draftRevision'] = revision + 1
            updated['version'] = version(updated)
            if row:
                row.configuration = updated
                row.save(using=database, update_fields=['configuration', 'updated_at'])
            else:
                ModuleConfigurationDraft.objects.using(database).create(
                    actor_key=SHARED_DRAFT_KEY, base_version=base, configuration=updated)
    except IntegrityError:
        return _error('The shared draft was created concurrently. Reload before saving.', 409)
    return JsonResponse({'structure': updated, 'baseVersion': base,
                         'revision': updated['_draftRevision'], 'scope': 'shared'})


@enrolment_login_required
@csrf_protect
def programme_structure_publish(request):
    """Gated adapter to the existing append-only Wizard Layout publisher."""
    if not getattr(settings, 'PROGRAMME_STRUCTURE_PUBLISHING_ENABLED', False):
        return _error('Programme Structure publishing is disabled.', 403)
    if not is_staff(request):
        return _error('Admissions staff required.', 403)
    if request.method != 'POST':
        return _error('Method not allowed.', 405)
    try:
        data = json.loads(request.body or '{}')
        structure = data.get('structure') if isinstance(data, dict) else None
        base = data.get('baseVersion') if isinstance(data, dict) else None
        from programme_suitability.publication import build_published_layout, preflight
        from enrolment_api.wizard_layout import CONN, read_current
        with connections[CONN].cursor() as cursor:
            current = read_current(cursor)
        current_version = current[0] if current else None
        current_layout = current[1] if current else {}
        expected = data.get('revision')
        if type(expected) is not int or expected < 1:
            return _error('Save the shared draft and provide its current revision before publishing.', 409)
        # Lock and publish on the publisher's connection, so the shared draft
        # cannot change between validation and the append-only transaction.
        with transaction.atomic(using=CONN):
            row = ModuleConfigurationDraft.objects.using(CONN).select_for_update().filter(pk=SHARED_DRAFT_KEY).first()
            if not row or row.base_version != base or row.configuration.get('_draftRevision') != expected or row.configuration != structure:
                return _error('The shared draft changed or contains unsaved edits. Save/reload and review it again.', 409)
            result = preflight({'programmeStructure': structure}, current_version=current_version,
                               base_version=base, draft_revision=row.configuration.get('_draftRevision'),
                               expected_revision=expected)
            merged = build_published_layout(current_layout, result)
            request._body = json.dumps({'layout': merged, 'baseVersion': base}).encode()
            from enrolment_api.wizard_layout import publish_wizard_layout
            response = publish_wizard_layout(request)
            if response.status_code < 400:
                # The official publication and Curriculum status update share
                # this transaction. Draft/Ignore never change assignment
                # eligibility; a failure rolls back the newly published row.
                from programme_suitability.publication import sync_published_cohort_statuses
                with connections[CONN].cursor() as cursor:
                    sync_published_cohort_statuses(cursor, current_layout, structure)
                payload = json.loads(response.content)
                row.base_version = payload['version']
                row.configuration = dict(row.configuration, _draftRevision=expected + 1)
                row.save(using=CONN, update_fields=['base_version', 'configuration', 'updated_at'])
                payload['revision'] = expected + 1
                payload['structure'] = row.configuration
                response = JsonResponse(payload)
            return response
    except Exception as exc:
        from programme_suitability.publication import PublicationBlocked
        if isinstance(exc, PublicationBlocked):
            return _error(str(exc), 409)
        return _error('Programme Structure publication preflight failed.', 400)
