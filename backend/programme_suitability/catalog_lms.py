"""Explicit LMS import and manual sync for the Programme Catalogue."""
from __future__ import annotations

import re
from datetime import date, time
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from enrollment_service.lms_catalogue_source import (
    SOURCE_BRANCH_ID, SOURCE_PROJECT_ID, SOURCE_SYSTEM,
    canonical_fingerprint, read_catalogue_snapshot,
)
from .catalog_models import (
    CatalogCohort, CatalogDelivery, CatalogGroup, CatalogKsb, CatalogModule,
    CatalogProgramme, CatalogSourceLink, CatalogWeeklyTopic, delivery_end,
    inclusive_month_end, validate_delivery_dates,
)
from .catalog_services import (
    calculate_cohort_schedule, validate_group_times, validate_module_topics,
    write_module_content,
)
from .catalog_serializers import ModuleCommercialCountsSerializer


ENTITY_MODELS = {
    'programme': CatalogProgramme,
    'cohort': CatalogCohort,
    'group': CatalogGroup,
    'ksb': CatalogKsb,
    'module_template': CatalogModule,
    'module_delivery': CatalogDelivery,
    'weekly_topic': CatalogWeeklyTopic,
}
WEEKDAYS = {name.lower(): index for index, name in enumerate(
    ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'), 1
)}


class CatalogLmsError(RuntimeError):
    def __init__(self, message, code, status=400, **details):
        self.code = code
        self.status = status
        self.details = details
        super().__init__(message)


def _level(value):
    match = re.search(r'[1-8]', str(value or ''))
    if not match:
        raise CatalogLmsError('The LMS programme has no supported apprenticeship level.', 'lms_missing_level')
    return int(match.group())


def _time(value, field):
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CatalogLmsError(f'The LMS {field} is missing or invalid.', 'lms_invalid_schedule') from exc
    return parsed.isoformat(timespec='minutes')


def _date(value, field):
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        raise CatalogLmsError(f'The LMS {field} is missing or invalid.', 'lms_invalid_date') from exc


def _iso_date(value, field):
    return _date(value, field).isoformat()


_WEIGHT_COUNTS = ('mapping_count', 'hard_count', 'soft_count', 'possible_count', 'main_count', 'secondary_count')
_WEIGHT_TOTALS = ('total_weight', 'hard_weight', 'soft_weight', 'possible_weight', 'main_weight', 'secondary_weight')


def _mapping_weight(value):
    try:
        if value['missing_weight_count'] != 0:
            raise ValueError('A source mapping has no weight.')
        counts = {key: value[key] for key in _WEIGHT_COUNTS}
        has_possible_classification = ('possible_classification_count' in value or
                                       'possible_classification_weight' in value)
        if has_possible_classification:
            counts['possible_classification_count'] = value['possible_classification_count']
        elif counts['possible_count'] == 0:
            counts['possible_classification_count'] = 0
        else:
            raise ValueError('Possible mappings have no classification breakdown.')
        if any(type(item) is not int or item < 0 for item in counts.values()):
            raise ValueError('Invalid source mapping counts.')
        weights = {key: value[key] for key in _WEIGHT_TOTALS}
        weights['possible_classification_weight'] = (value['possible_classification_weight']
                                                     if has_possible_classification else '0')
        if any(not isinstance(item, str) for item in weights.values()):
            raise ValueError('Source weights must retain their decimal representation.')
        numbers = {key: Decimal(item) for key, item in weights.items()}
        if any(not number.is_finite() or number < 0 for number in numbers.values()):
            raise ValueError('Invalid source mapping weight.')
        if any(counts[count_key] == 0 and numbers[weight_key] != 0 for count_key, weight_key in (
                ('hard_count', 'hard_weight'), ('soft_count', 'soft_weight'),
                ('possible_count', 'possible_weight'), ('main_count', 'main_weight'),
                ('secondary_count', 'secondary_weight'),
                ('possible_classification_count', 'possible_classification_weight'))):
            raise ValueError('A source weight has no corresponding mappings.')
        if (counts['mapping_count'] == 0 or
                sum(counts[key] for key in ('hard_count', 'soft_count', 'possible_count')) != counts['mapping_count'] or
                counts['main_count'] + counts['secondary_count'] +
                counts['possible_classification_count'] != counts['mapping_count'] or
                sum(numbers[key] for key in ('hard_weight', 'soft_weight', 'possible_weight')) != numbers['total_weight'] or
                sum(numbers[key] for key in ('main_weight', 'secondary_weight',
                                            'possible_classification_weight')) != numbers['total_weight']):
            raise ValueError('Incomplete source mapping breakdown.')
        result = {**{key: counts[key] for key in _WEIGHT_COUNTS},
                  **{key: weights[key] for key in _WEIGHT_TOTALS}}
        if counts['possible_classification_count']:
            result['possible_classification_count'] = counts['possible_classification_count']
            result['possible_classification_weight'] = weights['possible_classification_weight']
        return result
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise CatalogLmsError('The LMS KSB weight aggregation is incomplete or invalid.',
                              'lms_ksb_weight_incomplete', 409) from exc


def normalize_snapshot(raw, *, programme_id, cohort_start_date, standard_ref, standard_version):
    source_programme = raw['programme']
    if source_programme['programme_id'] != programme_id:
        raise CatalogLmsError('The LMS returned a different programme.', 'lms_source_identity_conflict', 409)
    if source_programme['status'] != 'active':
        raise CatalogLmsError('The selected LMS programme is not active.', 'lms_programme_inactive', 409)

    cohorts, groups, modules = {}, {}, {}
    for row in raw['hierarchy']:
        if row['cohort_status'] not in ('active', 'planned'):
            continue
        cohort_id = row['cohort_id']
        cohorts[cohort_id] = {
            'id': cohort_id, 'name': row['cohort_name'],
            'start_date': _iso_date(row['start_date'], 'cohort start date'),
            'training_end_date': _iso_date(row['end_date'], 'cohort training end date'),
            'training_duration_months': row['duration_months'], 'epa_period': row['epa_months'] or 0,
            'apprenticeship_end_date': _iso_date(row['apprenticeship_end_date'], 'apprenticeship end date') if row['apprenticeship_end_date'] else None,
            'source_revision': str(row['cohort_updated_at']),
        }
        if not row['group_id'] or row['group_status'] != 'active':
            continue
        group_id = row['group_id']
        weekday = WEEKDAYS.get(str(row['session_week_day'] or '').strip().lower())
        if not weekday:
            raise CatalogLmsError(f'Group {row["group_name"]} has no valid weekly delivery day.', 'lms_invalid_schedule')
        groups[group_id] = {
            'id': group_id, 'cohort_id': cohort_id, 'name': row['group_name'], 'weekday': weekday,
            'start_time': _time(row['session_start_time'], 'group start time'),
            'end_time': _time(row['session_end_time'], 'group end time'),
            'source_revision': str(row['group_updated_at']),
        }
        if not row['module_catalogue_id'] or row['module_status'] != 'active':
            continue
        module_id = row['module_catalogue_id']
        modules[module_id] = {
            'id': module_id, 'group_id': group_id, 'name': row['module_title'],
            'sessions': row['sessions_number'],
            'start_date': _iso_date(row['module_start_date'], 'module start date'),
            'end_date': _iso_date(row['module_end_date'], 'module end date'),
            'source_revision': str(row['module_updated_at']), 'weekly_topics': [],
            'ksb_ids': [], 'ksb_weights': {},
        }

    for week in raw['weeks']:
        module = modules.get(week['module_catalogue_id'])
        if module is not None:
            module['weekly_topics'].append({
                'id': week['id'], 'week': week['week_number'], 'content': week['title'],
                'source_revision': str(week['updated_at']),
            })

    ksbs = {}
    for row in raw['ksbs']:
        source_id = str(row['id'])
        ksbs[source_id] = {
            'id': source_id, 'code': row['ksb_code'], 'kind': row['ksb_type'],
            'description': row['ksb_description'], 'standard': row['standard_ref'],
            'standard_version': row['standard_version'], 'source_revision': str(row['updated_at']),
        }
        if not isinstance(row.get('module_weights'), list):
            raise CatalogLmsError('The LMS KSB weight aggregation is missing.', 'lms_ksb_weight_incomplete', 409)
        weighted = {}
        for item in row['module_weights']:
            module_id = item.get('module_id') if isinstance(item, dict) else None
            if not isinstance(module_id, str):
                raise CatalogLmsError('The LMS KSB weight aggregation is invalid.', 'lms_ksb_weight_incomplete', 409)
            if module_id in weighted:
                raise CatalogLmsError('The LMS returned duplicate module KSB weights.', 'lms_ksb_weight_incomplete', 409)
            weighted[module_id] = _mapping_weight(item)
        if set(weighted) != set(row['module_ids']):
            raise CatalogLmsError('The LMS module KSB weights do not match its coverage.',
                                  'lms_ksb_weight_incomplete', 409)
        for module_id in row['module_ids']:
            if module_id in modules:
                modules[module_id]['ksb_ids'].append(source_id)
                modules[module_id]['ksb_weights'][source_id] = weighted[module_id]
    for module in modules.values():
        module['weekly_topics'].sort(key=lambda item: (item['week'], item['id']))
        module['ksb_ids'].sort()

    programme = {
        'id': programme_id, 'name': source_programme['name'], 'code': programme_id,
        'level': _level(source_programme['level']), 'description': source_programme['description'] or '',
        'standard': standard_ref, 'standard_version': standard_version,
        'source_revision': str(source_programme['updated_at']),
        '_scope': {'cohort_start_date': cohort_start_date.isoformat()},
    }
    normalized = {
        'programme': programme,
        'cohorts': sorted(cohorts.values(), key=lambda item: (item['start_date'], item['id'])),
        'groups': sorted(groups.values(), key=lambda item: item['id']),
        'modules': sorted(modules.values(), key=lambda item: item['id']),
        'ksbs': sorted(ksbs.values(), key=lambda item: item['code']),
    }
    normalized['counts'] = {
        'programmes': 1, 'cohorts': len(cohorts), 'groups': len(groups),
        'modules': len(modules),
        'weekly_topics': sum(len(item['weekly_topics']) for item in modules.values()),
        'ksbs': len(ksbs),
    }
    normalized['fingerprint'] = canonical_fingerprint(normalized)
    return normalized


def _state(entity_type, row):
    ignored = {'source_revision'}
    if entity_type != 'programme':
        ignored.add('_scope')
    state = {key: value for key, value in row.items() if key not in ignored and key != 'import_status'}
    if entity_type == 'module_template':
        return {
            **{key: state[key] for key in ('id', 'name', 'sessions', 'ksb_ids')},
            'ksb_weights': state['ksb_weights'],
            'weekly_topics': [{
                'id': topic['id'], 'week': topic['week'], 'content': topic['content'],
            } for topic in state['weekly_topics']],
        }
    if entity_type == 'module_delivery':
        return {key: state[key] for key in ('id', 'group_id', 'start_date', 'end_date')}
    return state


def _entities(snapshot):
    result = {('programme', snapshot['programme']['id']): snapshot['programme']}
    for entity_type, key in [('cohort', 'cohorts'), ('group', 'groups'), ('ksb', 'ksbs')]:
        result.update({(entity_type, row['id']): row for row in snapshot[key]})
    for module in snapshot['modules']:
        result[('module_template', module['id'])] = module
        result[('module_delivery', module['id'])] = module
        for topic in module['weekly_topics']:
            result[('weekly_topic', topic['id'])] = {**topic, 'module_id': module['id']}
    return result


def _actor(request):
    account = getattr(request, 'login_account', None)
    return str(getattr(account, 'id', '') or getattr(account, 'subject_id', '') or '')


def _source_links():
    return CatalogSourceLink.objects.filter(
        source_system=SOURCE_SYSTEM, source_project_id=SOURCE_PROJECT_ID,
        source_branch_id=SOURCE_BRANCH_ID,
    )


def preview_import(programme_id, cohort_start_date, standard_ref, standard_version, *, links_ready=True):
    raw = read_catalogue_snapshot(programme_id, cohort_start_date, standard_ref, standard_version)
    snapshot = normalize_snapshot(
        raw, programme_id=programme_id, cohort_start_date=cohort_start_date,
        standard_ref=standard_ref, standard_version=standard_version,
    )
    links = ({(row.source_entity_type, row.source_entity_id, row.local_entity_type): row
              for row in _source_links().filter(source_entity_id__in=[key[1] for key in _entities(snapshot)])}
             if links_ready else {})
    conflicts = []
    for entity_type, source_id in _entities(snapshot):
        local_types = [entity_type]
        status = 'linked' if any((entity_type, source_id, local) in links for local in local_types) else 'new'
        if entity_type == 'programme' and status == 'new' and CatalogProgramme.objects.filter(code=programme_id).exists():
            status = 'conflict'
            conflicts.append(f'Programme code {programme_id} already belongs to an unlinked catalogue record.')
        _entities(snapshot)[(entity_type, source_id)]['import_status'] = status
    missing = []
    if not snapshot['cohorts']:
        missing.append('No eligible cohorts were found for the selected start date.')
    if any(not module['weekly_topics'] for module in snapshot['modules']):
        missing.append('One or more modules has no active weekly topics.')
    return {
        'snapshot': snapshot, 'preview_fingerprint': snapshot['fingerprint'],
        'conflicts': conflicts, 'missing_fields': missing,
        'can_import': not conflicts and bool(snapshot['cohorts']),
        'metrics': raw['metrics'], 'performance_warnings': raw['performance_warnings'],
    }


def _link(entity_type, source, local_type, local, actor):
    state = _state(entity_type, source)
    CatalogSourceLink.objects.update_or_create(
        source_system=SOURCE_SYSTEM, source_project_id=SOURCE_PROJECT_ID,
        source_branch_id=SOURCE_BRANCH_ID, source_entity_type=entity_type,
        source_entity_id=source['id'], local_entity_type=local_type,
        defaults={
            'local_record_id': local.pk, 'accepted_state': state,
            'accepted_fingerprint': canonical_fingerprint(state),
            'source_revision': source.get('source_revision', ''),
            'local_revision': getattr(local, 'revision', 1),
            'last_synced_at': timezone.now(), 'last_synced_by': actor,
        },
    )


def _programme_values(source, pricing):
    try:
        total = Decimal(str(pricing['total_cost']))
        epa = Decimal(str(pricing['epa_cost']))
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise ValidationError({'pricing': 'Enter valid Total per apprentice and EPA cost values.'}) from exc
    if total < 0 or epa < 0 or epa > total or total != total.to_integral_value() or epa != epa.to_integral_value():
        raise ValidationError({'pricing': 'Costs must be whole pounds and EPA cost cannot exceed the total.'})
    funding = pricing.get('funding_band_maximum')
    if funding not in (None, ''):
        funding = Decimal(str(funding))
        if funding < 0 or funding != funding.to_integral_value():
            raise ValidationError({'funding_band_maximum': 'Enter a whole-pound funding band maximum.'})
    return {
        'name': source['name'], 'code': source['code'], 'level': source['level'],
        'description': source['description'], 'total_cost': total, 'epa_cost': epa,
        'epao': str(pricing.get('epao') or '').strip(), 'funding_band_maximum': funding,
        'status': CatalogProgramme.Status.DRAFT,
    }


def _create_cohort(programme, source):
    start = _date(source['start_date'], 'cohort start date')
    duration = source['training_duration_months']
    if type(duration) is not int or not 1 <= duration <= 120:
        raise CatalogLmsError(f'Cohort {source["name"]} has an invalid duration.', 'lms_invalid_duration')
    automatic_end = inclusive_month_end(start, duration)
    source_end = _date(source['training_end_date'], 'cohort training end date')
    automatic_apprenticeship = inclusive_month_end(start, duration + source['epa_period'])
    source_apprenticeship = _date(source['apprenticeship_end_date'], 'apprenticeship end date') if source['apprenticeship_end_date'] else automatic_apprenticeship
    schedule = calculate_cohort_schedule(
        start, duration, source['epa_period'],
        training_end_override=source_end if source_end != automatic_end else None,
        apprenticeship_end_override=source_apprenticeship if source_apprenticeship != automatic_apprenticeship else None,
    )
    return CatalogCohort.objects.create(
        programme=programme, name=source['name'], start_date=start,
        epa_period=source['epa_period'], **schedule,
        source_system=SOURCE_SYSTEM, external_id=source['id'],
        source_revision=source['source_revision'], last_synced_at=timezone.now(),
    )


def _create_group(cohort, source):
    start, end = time.fromisoformat(source['start_time']), time.fromisoformat(source['end_time'])
    validate_group_times(start, end)
    return CatalogGroup.objects.create(
        cohort=cohort, name=source['name'], weekday=source['weekday'],
        start_time=start, end_time=end, source_system=SOURCE_SYSTEM,
        external_id=source['id'], source_revision=source['source_revision'], last_synced_at=timezone.now(),
    )


def _create_ksb(programme, source):
    return CatalogKsb.objects.create(
        programme=programme, code=source['code'], description=source['description'],
        kind=source['kind'], standard=source['standard'], standard_version=source['standard_version'],
        source_system=SOURCE_SYSTEM, external_id=source['id'],
        source_revision=source['source_revision'], last_synced_at=timezone.now(),
    )


def _create_module(programme, source, ksb_by_source, counts=None):
    weeks = max(1, source['sessions'] or 0, *(topic['week'] for topic in source['weekly_topics']))
    validate_module_topics(weeks, [item['week'] for item in source['weekly_topics']])
    module = CatalogModule.objects.create(
        programme=programme, name=source['name'], weeks=weeks, lectures=source['sessions'],
        books=counts['books'] if counts is not None else None,
        reviews=counts['reviews'] if counts is not None else None,
        source_system=SOURCE_SYSTEM, external_id=source['id'],
        source_revision=source['source_revision'], last_synced_at=timezone.now(),
    )
    topics = [{'week': item['week'], 'content': item['content']} for item in source['weekly_topics']]
    revision = write_module_content(
        module, ksb_ids=[ksb_by_source[item].pk for item in source['ksb_ids']], topics=topics,
        ksb_weights={str(ksb_by_source[item].pk): source['ksb_weights'][item] for item in source['ksb_ids']},
    )
    return module, revision


@transaction.atomic
def approve_import(preview_fingerprint, programme_id, cohort_start_date, standard_ref, standard_version,
                   pricing, actor, *, module_counts=None, acknowledge_performance_warnings=False):
    preview = preview_import(programme_id, cohort_start_date, standard_ref, standard_version)
    if preview['preview_fingerprint'] != preview_fingerprint:
        raise CatalogLmsError('The LMS data changed after preview. Preview the import again.', 'lms_preview_stale', 409)
    if preview['performance_warnings'] and not acknowledge_performance_warnings:
        raise CatalogLmsError(
            'Review and acknowledge the reported LMS performance warnings before import.',
            'lms_performance_review_required', 409,
            performance_warnings=preview['performance_warnings'],
        )
    if not preview['can_import']:
        raise CatalogLmsError('Resolve the import conflicts before approval.', 'lms_import_conflict', 409)
    snapshot = preview['snapshot']
    existing_link = _source_links().select_for_update().filter(
        source_entity_type='programme', source_entity_id=programme_id, local_entity_type='programme',
    ).first()
    if existing_link:
        programme = CatalogProgramme.objects.get(pk=existing_link.local_record_id)
        return {'programme_id': str(programme.pk), 'created': False, 'counts': snapshot['counts']}

    expected_modules = {module['id'] for module in snapshot['modules']}
    if not isinstance(module_counts, dict) or set(module_counts) != expected_modules:
        raise ValidationError({'module_counts': 'Enter Progress Reviews and Books for every previewed module.'})
    validated_counts = {}
    for module_id, values in module_counts.items():
        serializer = ModuleCommercialCountsSerializer(data=values)
        serializer.is_valid(raise_exception=True)
        validated_counts[module_id] = serializer.validated_data

    programme = CatalogProgramme.objects.create(
        **_programme_values(snapshot['programme'], pricing), source_system=SOURCE_SYSTEM,
        external_id=programme_id, source_revision=snapshot['programme']['source_revision'],
        last_synced_at=timezone.now(),
    )
    cohort_by_source = {row['id']: _create_cohort(programme, row) for row in snapshot['cohorts']}
    group_by_source = {row['id']: _create_group(cohort_by_source[row['cohort_id']], row) for row in snapshot['groups']}
    ksb_by_source = {row['id']: _create_ksb(programme, row) for row in snapshot['ksbs']}
    modules = {}
    deliveries = {}
    topics_by_source = {}
    for source in snapshot['modules']:
        module, revision = _create_module(programme, source, ksb_by_source, validated_counts[source['id']])
        modules[source['id']] = module
        for topic, local in zip(source['weekly_topics'], module.weekly_topics.order_by('week', 'id')):
            topics_by_source[topic['id']] = local
        start, source_end = _date(source['start_date'], 'module start date'), _date(source['end_date'], 'module end date')
        group = group_by_source[source['group_id']]
        validate_delivery_dates(
            start, source_end, group.cohort.start_date, group.cohort.effective_training_end_date,
            weeks=revision.weeks,
        )
        automatic_end = delivery_end(start, revision.weeks)
        deliveries[source['id']] = CatalogDelivery.objects.create(
            group=group, module=module, module_revision=revision,
            start_date=start, end_date_override=source_end if source_end != automatic_end else None,
            source_system=SOURCE_SYSTEM, external_id=source['id'],
            source_revision=source['source_revision'], last_synced_at=timezone.now(),
        )

    _link('programme', snapshot['programme'], 'programme', programme, actor)
    for source in snapshot['cohorts']:
        _link('cohort', source, 'cohort', cohort_by_source[source['id']], actor)
    for source in snapshot['groups']:
        _link('group', source, 'group', group_by_source[source['id']], actor)
    for source in snapshot['ksbs']:
        _link('ksb', source, 'ksb', ksb_by_source[source['id']], actor)
    for source in snapshot['modules']:
        _link('module_template', source, 'module_template', modules[source['id']], actor)
        _link('module_delivery', source, 'module_delivery', deliveries[source['id']], actor)
        for topic in source['weekly_topics']:
            _link('weekly_topic', {**topic, 'module_id': source['id']}, 'weekly_topic', topics_by_source[topic['id']], actor)
    return {'programme_id': str(programme.pk), 'created': True, 'counts': snapshot['counts']}


def _field_changes(previous, current):
    changes = []
    for key in sorted(set(previous) | set(current)):
        if key == 'ksb_weights':
            before, after = previous.get(key) or {}, current.get(key) or {}
            for ksb_id in sorted(set(before) | set(after)):
                if before.get(ksb_id) != after.get(ksb_id):
                    effective_changed = _hard_weight(before.get(ksb_id)) != _hard_weight(after.get(ksb_id))
                    changes.append({'field': f'KSB {ksb_id} ' +
                                    ('mapping weight' if effective_changed else 'source mapping details'),
                                    'impact': 'hard_readiness' if effective_changed else 'source_audit_only',
                                    'before': before.get(ksb_id), 'after': after.get(ksb_id)})
        elif previous.get(key) != current.get(key):
            changes.append({'field': key, 'before': previous.get(key), 'after': current.get(key)})
    return changes


def _hard_weight(value):
    if not isinstance(value, dict) or 'hard_count' not in value or 'hard_weight' not in value:
        return None
    return value['hard_count'], value['hard_weight']


def _module_curriculum_changed(previous, current):
    if any(previous.get(key) != current.get(key)
           for key in ('name', 'sessions', 'ksb_ids', 'weekly_topics')):
        return True
    old_weights, new_weights = previous.get('ksb_weights') or {}, current.get('ksb_weights') or {}
    return any(_hard_weight(old_weights.get(ksb_id)) != _hard_weight(new_weights.get(ksb_id))
               for ksb_id in set(old_weights) | set(new_weights))


def repin_future_deliveries(module, revision):
    """Use the approved revision for scheduled delivery, leaving started history intact."""
    upcoming = list(CatalogDelivery.objects.select_for_update().filter(
        module=module, archived_at__isnull=True, start_date__gt=timezone.localdate(),
    ))
    if not upcoming:
        return 0
    links = {row.local_record_id: row for row in _source_links().select_for_update().filter(
        local_entity_type='module_delivery', local_record_id__in=[item.pk for item in upcoming],
    )}
    if any(delivery.pk not in links or links[delivery.pk].local_revision != delivery.revision
           for delivery in upcoming):
        raise CatalogLmsError('A scheduled module link changed. Reload before updating its revision.',
                              'catalog_revision_conflict', 409)
    CatalogDelivery.objects.filter(pk__in=[item.pk for item in upcoming]).update(
        module_revision=revision, revision=F('revision') + 1, updated_at=timezone.now(),
    )
    _source_links().filter(pk__in=[row.pk for row in links.values()]).update(
        local_revision=F('local_revision') + 1, updated_at=timezone.now(),
    )
    return len(upcoming)


def check_updates(programme):
    root = _source_links().filter(local_entity_type='programme', local_record_id=programme.pk).first()
    if not root:
        raise CatalogLmsError('This programme is not linked to LMS.', 'lms_not_linked', 404)
    scope = root.accepted_state.get('_scope') or {}
    start = _date(scope.get('cohort_start_date'), 'linked cohort filter')
    standard_ref = root.accepted_state.get('standard')
    standard_version = root.accepted_state.get('standard_version')
    raw = read_catalogue_snapshot(root.source_entity_id, start, standard_ref, standard_version)
    snapshot = normalize_snapshot(
        raw, programme_id=root.source_entity_id, cohort_start_date=start,
        standard_ref=standard_ref, standard_version=standard_version,
    )
    current = _entities(snapshot)
    cohort_ids = list(programme.cohorts.values_list('id', flat=True))
    group_ids = list(CatalogGroup.objects.filter(cohort_id__in=cohort_ids).values_list('id', flat=True))
    module_ids = list(programme.curriculum_modules.values_list('id', flat=True))
    topic_ids = list(CatalogWeeklyTopic.objects.filter(module_id__in=module_ids).values_list('id', flat=True))
    delivery_ids = list(CatalogDelivery.objects.filter(group_id__in=group_ids).values_list('id', flat=True))
    ksb_ids = list(programme.ksbs.values_list('id', flat=True))
    local_ids = [programme.pk, *cohort_ids, *group_ids, *module_ids, *topic_ids, *delivery_ids, *ksb_ids]
    links = list(_source_links().filter(local_record_id__in=local_ids))
    changes = []
    seen = set()
    for link in links:
        if link.source_entity_type == 'weekly_topic':
            # Weekly Topic changes are represented once in the parent module's
            # field-level diff so one approval creates exactly one revision.
            continue
        key = (link.source_entity_type, link.source_entity_id)
        seen.add((link.source_entity_type, link.source_entity_id, link.local_entity_type))
        source = current.get(key)
        if source is None:
            changes.append({'key': f'{link.source_entity_type}:{link.source_entity_id}:{link.local_entity_type}',
                            'state': 'missing_source', 'entity_type': link.source_entity_type,
                            'source_id': link.source_entity_id, 'fields': []})
            continue
        fields = _field_changes(link.accepted_state, _state(link.source_entity_type, source))
        model = ENTITY_MODELS[link.local_entity_type]
        local = model.objects.filter(pk=link.local_record_id).first()
        state = 'conflict' if local is None or getattr(local, 'revision', 1) != link.local_revision else 'modified' if fields else 'unchanged'
        if state != 'unchanged':
            changes.append({'key': f'{link.source_entity_type}:{link.source_entity_id}:{link.local_entity_type}',
                            'state': state, 'entity_type': link.source_entity_type,
                            'source_id': link.source_entity_id, 'fields': fields})
    for (entity_type, source_id), source in current.items():
        if entity_type == 'weekly_topic':
            continue
        local_type = entity_type
        if (entity_type, source_id, local_type) not in seen:
            changes.append({'key': f'{entity_type}:{source_id}:{local_type}', 'state': 'new',
                            'entity_type': entity_type, 'source_id': source_id, 'fields': []})
    return {
        'programme_id': str(programme.pk), 'preview_fingerprint': snapshot['fingerprint'],
        'changes': changes, 'has_changes': bool(changes), 'snapshot': snapshot,
        'metrics': raw['metrics'], 'performance_warnings': raw['performance_warnings'],
    }


@transaction.atomic
def apply_updates(programme, preview_fingerprint, selected_keys, actor, expected_revision, *, archive_keys=()):
    locked = CatalogProgramme.objects.select_for_update().get(pk=programme.pk)
    if locked.revision != expected_revision:
        raise CatalogLmsError('The programme changed after the update review.', 'catalog_revision_conflict', 409)
    comparison = check_updates(locked)
    if comparison['preview_fingerprint'] != preview_fingerprint:
        raise CatalogLmsError('The LMS data changed after the update review.', 'lms_preview_stale', 409)
    selected = set(selected_keys)
    archives = set(archive_keys)
    available = {item['key']: item for item in comparison['changes']}
    if not selected.issubset(available) or not archives.issubset(available):
        raise CatalogLmsError('One or more selected changes is no longer available.', 'lms_change_invalid', 409)
    if any(available[key]['state'] in ('conflict', 'missing_source') for key in selected):
        raise CatalogLmsError('Conflicts and missing source records require manual review and cannot be applied automatically.', 'lms_change_conflict', 409)
    if any(available[key]['state'] != 'missing_source' or available[key]['entity_type'] == 'programme' for key in archives):
        raise CatalogLmsError('Only missing child records can be explicitly archived.', 'lms_archive_invalid', 409)

    entities = _entities(comparison['snapshot'])
    applied = 0
    links_by_source = {(item.source_entity_type, item.source_entity_id, item.local_entity_type): item
                       for item in _source_links().select_for_update()}

    def linked(entity_type, source_id):
        link = links_by_source.get((entity_type, source_id, entity_type))
        return ENTITY_MODELS[entity_type].objects.get(pk=link.local_record_id) if link else None

    new_changes = [available[key] for key in selected if available[key]['state'] == 'new']
    for entity_type in ('cohort', 'group', 'ksb', 'module_template', 'module_delivery'):
        for change in [item for item in new_changes if item['entity_type'] == entity_type]:
            source = entities[(entity_type, change['source_id'])]
            if entity_type == 'cohort':
                local = _create_cohort(locked, source)
            elif entity_type == 'group':
                parent = linked('cohort', source['cohort_id'])
                if parent is None:
                    raise CatalogLmsError('Select the new parent cohort before its group.', 'lms_change_dependency', 409)
                local = _create_group(parent, source)
            elif entity_type == 'ksb':
                local = _create_ksb(locked, source)
            elif entity_type == 'module_template':
                ksb_map = {source_id: linked('ksb', source_id) for source_id in source['ksb_ids']}
                if any(value is None for value in ksb_map.values()):
                    raise CatalogLmsError('Select all new module KSBs before the module.', 'lms_change_dependency', 409)
                local, _revision = _create_module(locked, source, ksb_map)
                for topic, local_topic in zip(source['weekly_topics'], local.weekly_topics.order_by('week', 'id')):
                    _link('weekly_topic', {**topic, 'module_id': source['id']}, 'weekly_topic', local_topic, actor)
            else:
                group = linked('group', source['group_id'])
                module = linked('module_template', source['id'])
                if group is None or module is None:
                    raise CatalogLmsError('Select the new module template and parent group before its delivery.', 'lms_change_dependency', 409)
                revision = module.revisions.first()
                start, end = _date(source['start_date'], 'module start date'), _date(source['end_date'], 'module end date')
                validate_delivery_dates(
                    start, end, group.cohort.start_date, group.cohort.effective_training_end_date,
                    weeks=revision.weeks,
                )
                local = CatalogDelivery.objects.create(
                    group=group, module=module, module_revision=revision, start_date=start,
                    end_date_override=end if end != delivery_end(start, revision.weeks) else None,
                    source_system=SOURCE_SYSTEM, external_id=source['id'],
                    source_revision=source['source_revision'], last_synced_at=timezone.now(),
                )
            _link(entity_type, source, entity_type, local, actor)
            links_by_source[(entity_type, source['id'], entity_type)] = _source_links().get(
                source_entity_type=entity_type, source_entity_id=source['id'], local_entity_type=entity_type,
            )
            applied += 1

    # Updates to existing linked records are intentionally field allowlisted.
    for key in selected:
        change = available[key]
        if change['state'] == 'new':
            continue
        entity_type, source_id = change['entity_type'], change['source_id']
        source = entities[(entity_type, source_id)]
        local_type = key.rsplit(':', 1)[-1]
        link = _source_links().select_for_update().get(
            source_entity_type=entity_type, source_entity_id=source_id, local_entity_type=local_type,
        )
        local = ENTITY_MODELS[local_type].objects.select_for_update().get(pk=link.local_record_id)
        if local_type == 'programme':
            for field in ('name', 'level', 'description'):
                setattr(local, field, source[field])
        elif local_type == 'cohort':
            start = _date(source['start_date'], 'cohort start date')
            automatic = inclusive_month_end(start, source['training_duration_months'])
            source_end = _date(source['training_end_date'], 'cohort training end date')
            automatic_apprenticeship = inclusive_month_end(
                start, source['training_duration_months'] + source['epa_period'],
            )
            source_apprenticeship = (_date(source['apprenticeship_end_date'], 'apprenticeship end date')
                                     if source['apprenticeship_end_date'] else automatic_apprenticeship)
            schedule = calculate_cohort_schedule(
                start, source['training_duration_months'], source['epa_period'], cohort=local,
                training_end_override=source_end if source_end != automatic else None,
                apprenticeship_end_override=(source_apprenticeship
                                             if source_apprenticeship != automatic_apprenticeship else None),
            )
            local.name, local.start_date = source['name'], start
            local.epa_period = source['epa_period']
            for field, value in schedule.items():
                setattr(local, field, value)
        elif local_type == 'group':
            local.name, local.weekday = source['name'], source['weekday']
            local.start_time, local.end_time = time.fromisoformat(source['start_time']), time.fromisoformat(source['end_time'])
            validate_group_times(local.start_time, local.end_time)
        elif local_type == 'ksb':
            for field in ('code', 'description', 'kind', 'standard', 'standard_version'):
                setattr(local, field, source[field])
        elif local_type == 'module_template':
            current_state = _state(entity_type, source)
            if not _module_curriculum_changed(link.accepted_state, current_state):
                # Preserve source-only Soft/Possible changes for auditing without
                # creating a new curriculum revision or repinning delivery.
                local.source_revision, local.last_synced_at = source['source_revision'], timezone.now()
                local.save(update_fields=['source_revision', 'last_synced_at'])
                link.accepted_state = current_state
                link.accepted_fingerprint = canonical_fingerprint(current_state)
                link.source_revision = source['source_revision']
                link.last_synced_at, link.last_synced_by = timezone.now(), actor
                link.save()
                applied += 1
                continue
            ksb_links = _source_links().filter(local_entity_type='ksb', source_entity_id__in=source['ksb_ids'])
            topics = [{'week': item['week'], 'content': item['content']} for item in source['weekly_topics']]
            local.name = source['name']
            local.weeks = max(1, source['sessions'] or 0, *(item['week'] for item in source['weekly_topics']))
            local.lectures = source['sessions']
            validate_module_topics(local.weeks, [item['week'] for item in source['weekly_topics']])
            local.revision += 1
            local.source_revision, local.last_synced_at = source['source_revision'], timezone.now()
            local.save()
            weight_ids = {item.source_entity_id: str(item.local_record_id) for item in ksb_links}
            if set(weight_ids) != set(source['ksb_ids']):
                raise CatalogLmsError('The linked KSB definitions are incomplete.', 'lms_change_dependency', 409)
            new_revision = write_module_content(
                local, ksb_ids=[weight_ids[item] for item in source['ksb_ids']], topics=topics,
                ksb_weights={weight_ids[item]: source['ksb_weights'][item] for item in source['ksb_ids']},
            )
            repin_future_deliveries(local, new_revision)
            topic_links = _source_links().filter(
                source_entity_type='weekly_topic', accepted_state__module_id=source['id'],
            )
            topic_by_week = {item.week: item for item in local.weekly_topics.filter(archived_at__isnull=True)}
            source_topics = {item['id']: item for item in source['weekly_topics']}
            for topic_link in topic_links:
                topic = source_topics.get(topic_link.source_entity_id)
                if topic is None:
                    continue
                local_topic = topic_by_week.get(topic['week'])
                if local_topic is not None:
                    topic_link.local_record_id = local_topic.pk
                    topic_link.accepted_state = _state('weekly_topic', {**topic, 'module_id': source['id']})
                    topic_link.accepted_fingerprint = canonical_fingerprint(topic_link.accepted_state)
                    topic_link.last_synced_at, topic_link.last_synced_by = timezone.now(), actor
                    topic_link.save()
            link.local_revision = local.revision
            link.accepted_state, link.accepted_fingerprint = _state(entity_type, source), canonical_fingerprint(_state(entity_type, source))
            link.source_revision, link.last_synced_at, link.last_synced_by = source['source_revision'], timezone.now(), actor
            link.save()
            applied += 1
            continue
        elif local_type in ('module_delivery', 'weekly_topic'):
            # Delivery dates and topics are applied through the template revision update;
            # existing deliveries deliberately retain their pinned revision.
            if local_type == 'module_delivery':
                local.start_date = _date(source['start_date'], 'module start date')
                end = _date(source['end_date'], 'module end date')
                validate_delivery_dates(
                    local.start_date, end, local.group.cohort.start_date,
                    local.group.cohort.effective_training_end_date, weeks=local.module_revision.weeks,
                )
                local.end_date_override = end if end != delivery_end(local.start_date, local.module_revision.weeks) else None
            else:
                local.week, local.content = source['week'], source['content']
        local.revision = getattr(local, 'revision', 0) + 1 if hasattr(local, 'revision') else getattr(local, 'revision', 1)
        local.source_revision = source.get('source_revision', getattr(local, 'source_revision', '')) if hasattr(local, 'source_revision') else ''
        if hasattr(local, 'last_synced_at'):
            local.last_synced_at = timezone.now()
        local.save()
        if local_type == 'ksb':
            for module in local.curriculum_modules.filter(source_system=SOURCE_SYSTEM, archived_at__isnull=True):
                module.revision += 1
                module.save(update_fields=['revision', 'updated_at'])
                new_revision = write_module_content(module)
                repin_future_deliveries(module, new_revision)
                _source_links().filter(local_entity_type='module_template', local_record_id=module.pk).update(
                    local_revision=module.revision, last_synced_at=timezone.now(), last_synced_by=actor,
                )
        link.local_revision = getattr(local, 'revision', 1)
        link.accepted_state, link.accepted_fingerprint = _state(entity_type, source), canonical_fingerprint(_state(entity_type, source))
        link.source_revision, link.last_synced_at, link.last_synced_by = source.get('source_revision', ''), timezone.now(), actor
        link.save()
        applied += 1
    for key in archives:
        change = available[key]
        local_type = key.rsplit(':', 1)[-1]
        link = _source_links().select_for_update().get(
            source_entity_type=change['entity_type'], source_entity_id=change['source_id'],
            local_entity_type=local_type,
        )
        local = ENTITY_MODELS[local_type].objects.select_for_update().get(pk=link.local_record_id)
        if not hasattr(local, 'archived_at'):
            raise CatalogLmsError('This missing source record cannot be archived automatically.', 'lms_archive_invalid', 409)
        if local.archived_at is None:
            local.archived_at = timezone.now()
            if hasattr(local, 'revision'):
                local.revision += 1
            local.save()
        link.local_revision = getattr(local, 'revision', 1)
        link.last_synced_at, link.last_synced_by = timezone.now(), actor
        link.save(update_fields=['local_revision', 'last_synced_at', 'last_synced_by', 'updated_at'])
        applied += 1
    locked.revision += 1
    locked.save(update_fields=['revision', 'updated_at'])
    _source_links().filter(local_entity_type='programme', local_record_id=locked.pk).update(
        local_revision=locked.revision, last_synced_at=timezone.now(), last_synced_by=actor,
    )
    return {'applied': applied, 'programme_id': str(locked.pk), 'programme_revision': locked.revision}
