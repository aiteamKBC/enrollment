import json
import logging
from django.db import connections, transaction, router, IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from enrolment_api.auth import enrolment_login_required
from enrolment_api.skills_radar.catalogue import CatalogueError
from programme_suitability.models import ModuleConfigurationDraft
from programme_suitability.programme_structure import KEY, version, validate_structure
from programme_suitability.permissions import is_staff
from django.conf import settings

logger = logging.getLogger(__name__)

# Programme Structure is shared system configuration, not a personal builder draft.
SHARED_DRAFT_KEY = 'programme-structure:shared'

VERIFIED_CURRICULUM_PROGRAMMES = {
    'ME_L4': ('PROG-ME-L4', 'Marketing Executive Level 4'),
    'MM_L6': ('PROG-MM-L6', 'Marketing Manager Level 6'),
    'APM_L4': ('PROG-20260907141619067932', 'Associate Project Manager Level 4'),
    'PCP_L6': ('PROG-PCP-L6', 'Project Controls Professional Level 6'),
}
TEST_COHORT_ID = 'COHORT-20261111015402258877CC69FD92DAF3'
TEST_COHORT_NAME = 'hisham of aa7'

def _actor(request):
    return SHARED_DRAFT_KEY

def _error(message, status=400): return JsonResponse({'error': message}, status=status)


def _normalise_identity(value):
    return ' '.join(str(value or '').split()).casefold()


def _curriculum_columns(cursor, schema, table):
    # Isolated tests attach the Curriculum schema to SQLite; PostgreSQL has
    # information_schema. Keep the same identity/reference checks on either.
    if getattr(cursor.db, 'vendor', None) == 'sqlite':
        try:
            cursor.execute(f'PRAGMA "{schema}".table_info("{table}")')
            return {row[1] for row in cursor.fetchall()}
        except Exception:
            return set()
    cursor.execute(
        'SELECT column_name FROM information_schema.columns '
        'WHERE table_schema = %s AND table_name = %s', [schema, table]
    )
    return {row[0] for row in cursor.fetchall()}


def _cohort_has_learner_reference(cursor, cohort_id, cohort_name):
    """Fail closed when a real learner placement references the test Cohort."""
    checks = (
        ('Learner', 'learners', ('cohort_id', 'cohort')),
        ('enrolment', 'Created_users', ('Cohort',)),
        ('enrolment', 'applications', ('cohort_id',)),
    )
    for schema, table, candidates in checks:
        columns = _curriculum_columns(cursor, schema, table)
        usable = [column for column in candidates if column in columns]
        if not usable:
            continue
        quoted = f'"{schema}"."{table}"'
        clauses = []
        params = []
        for column in usable:
            # Curriculum cohort IDs are strings, while some legacy learner
            # tables (notably enrolment.applications.cohort_id) store a
            # numeric internal learner-placement key.  Comparing the raw
            # values makes PostgreSQL attempt to cast a COHORT-* identity to
            # bigint and raises DataError before the correction can run.  A
            # text comparison is type-safe for both schemas and still keeps
            # the check exact (no name matching or fallback assignment).
            clauses.append(f'CAST("{column}" AS TEXT) IN (%s, %s)')
            params.extend([cohort_id, cohort_name])
        cursor.execute(f'SELECT 1 FROM {quoted} WHERE ' + ' OR '.join(clauses) + ' LIMIT 1', params)
        if cursor.fetchone():
            return True
    return False


def _verified_correction(structure, cursor):
    """Prepare the one-time correction without persisting it."""
    from copy import deepcopy

    if not isinstance(structure, dict) or not isinstance(structure.get('programmes'), list):
        raise ValueError('A complete shared Programme Structure is required.')
    programmes = {item.get('id'): item for item in structure['programmes'] if isinstance(item, dict)}
    missing = sorted(set(VERIFIED_CURRICULUM_PROGRAMMES) - set(programmes))
    if missing:
        raise ValueError('The shared draft is missing programmes: ' + ', '.join(missing))

    expected_rows = []
    for canonical_id, (curriculum_id, expected_name) in VERIFIED_CURRICULUM_PROGRAMMES.items():
        cursor.execute(
            'SELECT programme_id, name FROM curriculum.programmes WHERE programme_id = %s',
            [curriculum_id],
        )
        row = cursor.fetchone()
        if not row or row[0] != curriculum_id or _normalise_identity(row[1]) != _normalise_identity(expected_name):
            raise ValueError(f'Curriculum identity verification failed for {canonical_id}.')
        expected_rows.append((canonical_id, curriculum_id))

    corrected = deepcopy(structure)
    removed = []
    for programme in corrected['programmes']:
        canonical_id = programme.get('id')
        curriculum_id = dict(expected_rows)[canonical_id]
        programme['curriculumProgrammeId'] = curriculum_id
        retained = []
        for cohort in programme.get('cohorts', []) or []:
            cohort_id = cohort.get('id')
            if cohort_id == TEST_COHORT_ID and programme.get('id') == 'ME_L4':
                cursor.execute(
                    'SELECT cohort_id, cohort_name, programme_id FROM curriculum.cohorts WHERE cohort_id = %s',
                    [TEST_COHORT_ID],
                )
                test_row = cursor.fetchone()
                if not test_row:
                    raise ValueError('The test Cohort could not be verified in Curriculum.')
                if test_row[2] != curriculum_id or _normalise_identity(test_row[1]) != _normalise_identity(TEST_COHORT_NAME):
                    raise ValueError('The test Cohort identity does not match the expected ME_L4 record.')
                if _cohort_has_learner_reference(cursor, TEST_COHORT_ID, TEST_COHORT_NAME):
                    raise ValueError('The test Cohort has learner references and cannot be removed from the managed draft.')
                removed.append(TEST_COHORT_ID)
                continue
            if str(cohort_id or '').startswith('COHORT-'):
                cursor.execute(
                    'SELECT programme_id FROM curriculum.cohorts WHERE cohort_id = %s', [cohort_id]
                )
                row = cursor.fetchone()
                if not row or row[0] != curriculum_id:
                    raise ValueError(f'Cohort {cohort_id} does not belong to {canonical_id}.')
            retained.append(cohort)
        programme['cohorts'] = retained
    return corrected, removed

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
def programme_structure_reconcile(request):
    """Prepare the verified Curriculum identity correction for the shared draft.

    This endpoint deliberately does not save.  It returns a corrected local
    structure; the normal Save Changes request performs the revision-checked
    persistence.  Curriculum rows are read only and the test Cohort is never
    deleted.
    """
    if not is_staff(request):
        return _error('Admissions staff required.', 403)
    if request.method != 'POST':
        return _error('Method not allowed.', 405)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return _error('Request body must be valid JSON.')
    if not isinstance(data, dict) or type(data.get('revision')) is not int:
        return _error('The current shared draft revision is required.', 400)
    database = router.db_for_write(ModuleConfigurationDraft) or 'default'
    try:
        with transaction.atomic(using=database):
            row = ModuleConfigurationDraft.objects.using(database).select_for_update().filter(pk=SHARED_DRAFT_KEY).first()
            if not row:
                return _error('The shared draft does not exist. Reload before correcting it.', 409)
            revision = (row.configuration or {}).get('_draftRevision', 0)
            if data['revision'] != revision:
                return _error('This shared draft changed since you loaded it. Reload before correcting it.', 409)
            if data.get('baseVersion') is not None and data.get('baseVersion') != row.base_version:
                return _error('The shared settings changed. Reload before correcting it.', 409)
            from enrolment_api.wizard_layout import CONN, read_current
            with connections[CONN].cursor() as curriculum_cursor:
                current = read_current(curriculum_cursor)
                if current and row.base_version != current[0]:
                    return _error('Published settings changed. Reconcile the shared draft before correcting it.', 409)
                corrected, removed = _verified_correction(row.configuration, curriculum_cursor)
            return JsonResponse({
                'structure': corrected,
                'baseVersion': row.base_version,
                'revision': revision,
                'scope': 'shared',
                'persisted': False,
                'removedCohorts': removed,
                'verifiedCurriculumProgrammeIds': {k: v[0] for k, v in VERIFIED_CURRICULUM_PROGRAMMES.items()},
            })
    except ValueError as exc:
        return _error(str(exc), 409)
    except Exception:
        # Keep the response safe for Admin users while retaining the precise
        # database exception in server logs for diagnosis and recovery.
        logger.exception('Curriculum identity verification failed for shared Programme Structure draft.')
        return _error('Curriculum identity verification could not read the authoritative Curriculum records. Reload the draft and try again.', 503)


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
