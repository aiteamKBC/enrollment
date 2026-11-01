"""Pin published module content; the wizard continues owning learner choices."""
from django.db import transaction

from .configuration import ConfigurationError
from .models import Assessment
from .modules_configuration import KEY, defaults, legacy_defaults, validate
from .modules_rules import available_modules, validate_active_selection


def _enrolment_finalized(learner_id):
    from learner_api.models import EnrolmentUser
    status = EnrolmentUser.all_learners.filter(pk=learner_id).values_list('onboarding_status', flat=True).first()
    return str(status or '').strip().lower() in {'submitted', 'completed'}


def _has_complete_rules(row, choice):
    if not row.modules_configuration or not isinstance(choice, dict):
        return False
    programme = next((p for p in row.modules_configuration['programmes'] if p['id'] == 'PCP_L6'), {})
    path = next((p for p in programme.get('pathways', []) if p['id'] == choice.get('pathway')), {})
    return path.get('requiredOptionalModulesCount') is not None and selection_complete(row, choice)


def published_modules(validate_rules=True):
    from enrolment_api.wizard_layout import CONN, read_current
    from django.db import connections
    with connections[CONN].cursor() as cursor:
        current = read_current(cursor)
    return (current[0], validate(current[1].get(KEY) or defaults(), publishing=validate_rules)) if current else (None, validate(defaults()))


def saved_choices(kind, learner_id):
    from enrolment_api.models import ExtendedIlr
    row = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).only('wizard_draft').first()
    return (row.wizard_draft or {}).get(KEY) if row else None


def pinned_configuration(kind, learner_id):
    with transaction.atomic():
        row = Assessment.objects.select_for_update().filter(learner_id=learner_id).first()
        if row is None or not row.confirmed_at or not row.selection_confirmed_at:
            raise ConfigurationError('Confirm your programme before choosing modules.')
        stored = saved_choices(row.learner_kind, learner_id) if row.selected_programme == 'PCP_L6' else None
        historical = _enrolment_finalized(learner_id)
        if row.modules_configuration is None:
            legacy = stored
            # Choices predating configurable modules refer to the original names,
            # credits and hierarchy, not whatever was published subsequently.
            version, config = (0, validate(legacy_defaults())) if historical and legacy and 'configurationVersion' not in legacy else published_modules()
            row.modules_configuration = config
            row.modules_configuration_version = version
            row.save(update_fields=['modules_configuration', 'modules_configuration_version'])
        elif row.selected_programme == 'PCP_L6' and not historical and not _has_complete_rules(row, stored):
            version, config = published_modules()
            if version != row.modules_configuration_version:
                row.history = [*row.history, {'action': 'MODULE_CONFIGURATION_REFRESH',
                    'fromVersion': row.modules_configuration_version, 'toVersion': version,
                    'configuration': row.modules_configuration}]
                row.modules_configuration = config
                row.modules_configuration_version = version
                row.save(update_fields=['modules_configuration', 'modules_configuration_version', 'history'])
        return row, row.modules_configuration_version, row.modules_configuration


def _selectable(items):
    out = set()
    for item in items:
        if not item['active']:
            continue
        if item['selectable']:
            out.add(item['id'])
        out.update(_selectable(item.get('items', [])))
    return out


def validate_choices(kind, learner_id, raw, complete=False):
    if not isinstance(raw, dict) or raw.get('programme') != 'PCP_L6':
        raise ConfigurationError('Module choices must reference the confirmed PCP programme.')
    row, version, config = pinned_configuration(kind, learner_id)
    if row.selected_programme != 'PCP_L6':
        raise ConfigurationError('Module choices do not match your confirmed programme.')
    if 'configurationVersion' not in raw:
        legacy = saved_choices(row.learner_kind, learner_id)
        if not isinstance(legacy, dict) or 'configurationVersion' in legacy:
            raise ConfigurationError('Provide the module configuration version.')
    if 'configurationVersion' in raw and (type(raw['configurationVersion']) not in (int, type(None)) or raw['configurationVersion'] != version):
        raise ConfigurationError('Module configuration version does not match your saved enrolment.')
    return validate_snapshot_choices(raw, version, config, complete=complete)


def validate_snapshot_choices(raw, version, config, complete=False):
    if not isinstance(raw, dict) or raw.get('programme') != 'PCP_L6':
        raise ConfigurationError('Choose and save your PCP pathway and modules before continuing.')
    if 'configurationVersion' in raw and (type(raw['configurationVersion']) not in (int, type(None)) or raw['configurationVersion'] != version):
        raise ConfigurationError('Module configuration version does not match your saved enrolment.')
    paths = {p['id']: p for p in next(p for p in config['programmes'] if p['id'] == 'PCP_L6')['pathways']}
    active = raw.get('pathway')
    if not isinstance(active, str) or active not in paths or not paths[active]['active']:
        raise ConfigurationError('Choose an available pathway from your saved configuration.')
    if config.get('schemaVersion') == 2 and paths[active].get('requiredOptionalModulesCount') is None:
        raise ConfigurationError('The saved pathway is missing its required optional module count.')
    selections = raw.get('modulesByPathway', {})
    if not isinstance(selections, dict):
        raise ConfigurationError('Invalid module choices.')
    selections = {key: list(ids) if isinstance(ids, list) else ids for key, ids in selections.items()}
    for path_id, ids in selections.items():
        if path_id not in paths or not isinstance(ids, list) or any(not isinstance(mid, str) for mid in ids):
            raise ConfigurationError('Invalid pathway or module identifier.')
        if len(set(ids)) != len(ids) or not set(ids) <= _selectable(paths[path_id]['modules']):
            raise ConfigurationError('Module choices include unavailable or duplicate modules.')
        mandatory = {m['id'] for m in available_modules(paths[path_id]['modules']) if m.get('isMandatory')}
        if ids and (not paths[path_id]['active'] or (not paths[path_id]['allowModuleSelection'] and not set(ids) <= mandatory)):
            raise ConfigurationError('Module selection is disabled for this pathway.')
    # Other pathway choices are retained as inactive history, never included in
    # the active pathway's mandatory/count/credit calculation.
    mandatory = [m['id'] for m in available_modules(paths[active]['modules']) if m.get('isMandatory')]
    selections[active] = list(dict.fromkeys([*selections.get(active, []), *mandatory]))
    validate_active_selection(paths[active], selections.get(active, []), complete=complete)
    return {'programme': 'PCP_L6', 'pathway': active, 'modulesByPathway': selections, 'configurationVersion': version}


def selection_complete(row, choice):
    if row.selected_programme != 'PCP_L6':
        return True
    if row.modules_configuration is None:
        return False
    try:
        canonical = validate_snapshot_choices(choice, row.modules_configuration_version, row.modules_configuration, complete=True)
        return set(canonical['modulesByPathway'][canonical['pathway']]) == set(choice.get('modulesByPathway', {}).get(canonical['pathway'], []))
    except ConfigurationError:
        return False


def save_selection(kind, learner_id, raw):
    """Strict selection commit, atomically merging only modules into the draft.

    Both tables are in the same configured database; use the assessment's alias
    for this transaction rather than splitting it across Django aliases.
    """
    from enrolment_api.models import ExtendedIlr
    with transaction.atomic(using='default'):
        canonical = validate_choices(kind, learner_id, raw, complete=True)
        assessment = Assessment.objects.get(learner_id=learner_id)
        record, _ = ExtendedIlr.objects.using('default').select_for_update().get_or_create(
            learner_kind=assessment.learner_kind, learner_id=learner_id,
            defaults={'wizard_draft': {}})
        record.wizard_draft = {**(record.wizard_draft or {}), KEY: canonical}
        record.save(using='default', update_fields=['wizard_draft', 'updated_at'])
        return canonical
