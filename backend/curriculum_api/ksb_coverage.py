from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import json


SUPPORTED_CLASSIFICATIONS = {'main', 'secondary', 'possible'}
LEGACY_CLASSIFICATION_MAP = {'practice': 'possible'}
SUPPORTED_WEIGHT_CLASSES = {'hard', 'soft', 'possible'}


def clean(value):
    return str(value or '').strip()


def unique_clean(values):
    seen = set()
    result = []
    for value in values or []:
        text = clean(value)
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def component_group_names(component=None, module=None):
    component = component or {}
    module = module or {}
    settings = component.get('settings_json') or {}
    if isinstance(settings, str):
        try:
            settings = json.loads(settings) if settings else {}
        except (TypeError, ValueError):
            settings = {}
    if not isinstance(settings, dict):
        settings = {}
    names = settings.get('selectedGroupNames') if isinstance(settings.get('selectedGroupNames'), list) else []
    keys = settings.get('selectedGroupKeys') if isinstance(settings.get('selectedGroupKeys'), list) else []
    fallback = [module.get('group_name'), module.get('group_id')]
    return unique_clean([*names, *(keys if not names else []), *(fallback if not names and not keys else [])])


def normalise_code(value):
    return clean(value).upper()


def normalise_classification(value):
    raw = clean(value).lower()
    raw = LEGACY_CLASSIFICATION_MAP.get(raw, raw)
    return raw if raw in SUPPORTED_CLASSIFICATIONS else 'secondary'


def normalise_weight_class(value, classification=''):
    raw = clean(value).lower()
    if raw in SUPPORTED_WEIGHT_CLASSES:
        return raw
    classification = normalise_classification(classification)
    if classification == 'main':
        return 'hard'
    if classification == 'possible':
        return 'possible'
    return 'soft'


def decimal_weight(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return parsed.quantize(Decimal('0.01'))


def float_weight(value):
    parsed = decimal_weight(value)
    if parsed == parsed.to_integral():
        return int(parsed)
    return float(parsed)


def ksb_type_from_value(value, code=''):
    raw = clean(value).lower()
    if raw in {'knowledge', 'k'}:
        return 'knowledge'
    if raw in {'skill', 'skills', 's'}:
        return 'skill'
    if raw in {'behaviour', 'behaviours', 'behavior', 'behaviors', 'b'}:
        return 'behaviour'
    prefix = normalise_code(code)[:1]
    if prefix == 'S':
        return 'skill'
    if prefix == 'B':
        return 'behaviour'
    return 'knowledge'


def default_summary_bucket():
    # Coverage is reported as weight only. `mapped` counts the KSBs that carry
    # any weight at all; `total_weight` is the sum across the bucket. There is
    # deliberately no fully/partially-covered verdict: the weight is the fact,
    # and any threshold on top of it would be an assumption.
    return {
        'required': 0,
        'mapped': 0,
        'unmapped': 0,
        'total_weight': 0,
    }


def classification_summary(mappings):
    counts = Counter(mapping['classification'] for mapping in mappings)
    weights = defaultdict(Decimal)
    for mapping in mappings:
        weights[mapping['classification']] += decimal_weight(mapping.get('weight'))
    return {
        key: {
            'count': counts.get(key, 0),
            'weight': float_weight(weights.get(key, 0)),
        }
        for key in ('main', 'secondary', 'possible')
        if counts.get(key, 0) or weights.get(key, 0)
    }


def serialise_mapping(row, module=None, week=None, component=None):
    module = module or {}
    week = week or {}
    component = component or {}
    groups = component_group_names(component, module)
    mapping_level = 'component' if clean(row.get('component_id') or component.get('id')) else 'week' if clean(row.get('week_id') or week.get('id')) else 'module'
    return {
        'mapping_id': clean(row.get('id')),
        'mappingId': clean(row.get('id')),
        'programme_id': clean(module.get('programme_id')),
        'programmeId': clean(module.get('programme_id')),
        'programme_name': clean(module.get('programme_name')),
        'programmeName': clean(module.get('programme_name')),
        'module_id': clean(row.get('module_catalogue_id') or module.get('module_catalogue_id')),
        'moduleId': clean(row.get('module_catalogue_id') or module.get('module_catalogue_id')),
        'module_name': clean(module.get('title')),
        'moduleName': clean(module.get('title')),
        'group_id': clean(module.get('group_id')),
        'groupId': clean(module.get('group_id')),
        'group_name': clean(module.get('group_name')),
        'groupName': clean(module.get('group_name')),
        'groups': groups,
        'week_id': clean(row.get('week_id') or week.get('id')),
        'weekId': clean(row.get('week_id') or week.get('id')),
        'week_name': clean(week.get('title')) or (f"Week {week.get('week_number')}" if week.get('week_number') else ''),
        'weekName': clean(week.get('title')) or (f"Week {week.get('week_number')}" if week.get('week_number') else ''),
        'component_id': clean(row.get('component_id') or component.get('id')),
        'componentId': clean(row.get('component_id') or component.get('id')),
        'component_name': clean(component.get('title')),
        'componentName': clean(component.get('title')),
        'component_type': clean(component.get('type')),
        'componentType': clean(component.get('type')),
        # The OTJH the component this mapping sits on is expected to take. Only
        # component-level mappings carry it; week/module-level mappings are not
        # attached to a single component, so they report 0.
        'component_otjh': float_weight(component.get('expected_otjh')),
        'componentOtjh': float_weight(component.get('expected_otjh')),
        'ksb_id': clean(row.get('ksb_id') or row.get('ksb_code')),
        'ksbId': clean(row.get('ksb_id') or row.get('ksb_code')),
        'code': normalise_code(row.get('ksb_code')),
        'description': clean(row.get('ksb_description')),
        'source_type': clean(row.get('source_type')),
        'sourceType': clean(row.get('source_type')),
        'source_id': clean(row.get('source_id')),
        'sourceId': clean(row.get('source_id')),
        'classification': normalise_classification(row.get('classification')),
        'weight_class': normalise_weight_class(row.get('weight_class'), row.get('classification')),
        'weightClass': normalise_weight_class(row.get('weight_class'), row.get('classification')),
        'mapping_level': mapping_level,
        'mappingLevel': mapping_level,
        'weight': float_weight(row.get('weight')),
    }


def required_definition_from_mapping(mapping):
    return {
        'ksb_id': mapping.get('ksb_id') or mapping.get('code'),
        'ksbId': mapping.get('ksb_id') or mapping.get('code'),
        'code': mapping.get('code'),
        'title': mapping.get('code'),
        'description': mapping.get('description') or '',
        'ksb_type': ksb_type_from_value('', mapping.get('code')),
        'ksbType': ksb_type_from_value('', mapping.get('code')),
        'source_type': mapping.get('source_type') or '',
        'sourceType': mapping.get('source_type') or '',
        'source_id': mapping.get('source_id') or '',
        'sourceId': mapping.get('source_id') or '',
    }


def normalise_required_definition(item):
    code = normalise_code(item.get('code') or item.get('fullCode') or item.get('full_code'))
    ksb_type = ksb_type_from_value(item.get('ksb_type') or item.get('ksbType') or item.get('type'), code)
    return {
        'ksb_id': clean(item.get('ksb_id') or item.get('ksbId') or item.get('id') or code),
        'ksbId': clean(item.get('ksb_id') or item.get('ksbId') or item.get('id') or code),
        'code': code,
        'title': clean(item.get('title')) or code,
        'description': clean(item.get('description')),
        'ksb_type': ksb_type,
        'ksbType': ksb_type,
        'source_type': clean(item.get('source_type') or item.get('sourceType')),
        'sourceType': clean(item.get('source_type') or item.get('sourceType')),
        'source_id': clean(item.get('source_id') or item.get('sourceId')),
        'sourceId': clean(item.get('source_id') or item.get('sourceId')),
    }


def coverage_identity(item):
    return (
        clean(item.get('source_type') or item.get('sourceType')).lower(),
        clean(item.get('source_id') or item.get('sourceId')).lower(),
        normalise_code(item.get('code')),
    )


def coverage_sort_key(identity):
    source_type, source_id, code = identity
    return (ksb_sort_key(code), source_type, source_id)


def build_coverage_summary(required_ksbs, mapping_rows, include_mapping_only=True):
    """The same per-source KSB weights/counts without evidence or module cells."""
    definitions = {}
    for item in required_ksbs:
        definition = normalise_required_definition(item)
        if definition['code']:
            definitions[coverage_identity(definition)] = definition
    weights = defaultdict(Decimal)
    counts = Counter()
    for row in mapping_rows:
        mapping = {
            'code': normalise_code(row.get('ksb_code')),
            'source_type': clean(row.get('source_type')),
            'source_id': clean(row.get('source_id')),
        }
        identity = coverage_identity(mapping)
        count = row.get('_stats_count', 1)
        weights[identity] += decimal_weight(float_weight(row.get('weight'))) * count
        counts[identity] += count
        if include_mapping_only and mapping['code'] and identity not in definitions:
            definitions[identity] = required_definition_from_mapping(mapping)
    return {
        'view': 'summary',
        'items': [
            {**definitions[identity], 'coverage_key': '|'.join(identity),
             'raw_total_weight': float_weight(weights[identity]), 'occurrence_count': counts[identity]}
            for identity in sorted(definitions, key=coverage_sort_key)
        ],
    }


def build_coverage(required_ksbs, mapping_rows, module_rows, week_rows, component_rows, include_mapping_only=True, *, totals_only=False):
    modules_by_id = {clean(row.get('module_catalogue_id')): row for row in module_rows}
    module_authoring_weeks_by_id = {clean(row.get('id')): row for row in week_rows}
    components_by_id = {clean(row.get('id')): row for row in component_rows}

    serialised_mappings = []
    for row in mapping_rows:
        if not clean(row.get('module_catalogue_id')):
            continue
        if totals_only:
            # Same identity and per-mapping rounding as serialise_mapping. The
            # stats reader needs weights only, not mapping labels or a heatmap.
            serialised_mappings.append({
                'code': normalise_code(row.get('ksb_code')),
                'source_type': clean(row.get('source_type')),
                'source_id': clean(row.get('source_id')),
                'weight': float_weight(row.get('weight')),
                '_stats_count': row.get('_stats_count', 1),
            })
            continue
        module = modules_by_id.get(clean(row.get('module_catalogue_id')), {})
        week = module_authoring_weeks_by_id.get(clean(row.get('week_id')), {})
        component = components_by_id.get(clean(row.get('component_id')), {})
        serialised_mappings.append(serialise_mapping(row, module, week, component))

    definitions_by_identity = {}
    for item in required_ksbs:
        definition = normalise_required_definition(item)
        if definition['code']:
            definitions_by_identity[coverage_identity(definition)] = definition

    if include_mapping_only:
        for mapping in serialised_mappings:
            identity = coverage_identity(mapping)
            if mapping['code'] and identity not in definitions_by_identity:
                definitions_by_identity[identity] = required_definition_from_mapping(mapping)

    by_identity = defaultdict(list)
    for mapping in serialised_mappings:
        by_identity[coverage_identity(mapping)].append(mapping)

    items = []
    summary = {
        'overall': default_summary_bucket(),
        'knowledge': default_summary_bucket(),
        'skills': default_summary_bucket(),
        'behaviours': default_summary_bucket(),
    }

    for identity in sorted(definitions_by_identity, key=coverage_sort_key):
        definition = definitions_by_identity[identity]
        mappings = by_identity.get(identity, [])
        raw_total = sum(decimal_weight(mapping.get('weight')) * (
            mapping['_stats_count'] if totals_only else 1
        ) for mapping in mappings)
        if totals_only:
            items.append({'code': definition['code'], 'raw_total_weight': float_weight(raw_total)})
            continue
        module_ids = {mapping.get('module_id') for mapping in mappings if mapping.get('module_id')}
        week_ids = {mapping.get('week_id') for mapping in mappings if mapping.get('week_id')}
        component_ids = {mapping.get('component_id') for mapping in mappings if mapping.get('component_id')}
        occurrence_count = len(mappings)
        component_count = len(component_ids)
        item = {
            **definition,
            'coverage_key': '|'.join(identity),
            'coverageKey': '|'.join(identity),
            'raw_total_weight': float_weight(raw_total),
            'rawTotalWeight': float_weight(raw_total),
            'coverage_percentage': float_weight(raw_total),
            'coveragePercentage': float_weight(raw_total),
            'progress_bar_percentage': min(float(raw_total), 100),
            'progressBarPercentage': min(float(raw_total), 100),
            'occurrence_count': occurrence_count,
            'occurrenceCount': occurrence_count,
            'mapping_count': len(mappings),
            'mappingCount': len(mappings),
            'module_count': len(module_ids),
            'moduleCount': len(module_ids),
            'week_count': len(week_ids),
            'weekCount': len(week_ids),
            'component_count': component_count,
            'componentCount': component_count,
            'classification_summary': classification_summary(mappings),
            'classificationSummary': classification_summary(mappings),
            'mappings': mappings,
        }
        items.append(item)

        bucket_name = {
            'knowledge': 'knowledge',
            'skill': 'skills',
            'behaviour': 'behaviours',
        }.get(definition['ksb_type'], 'knowledge')
        for bucket in (summary['overall'], summary[bucket_name]):
            bucket['required'] += 1
            if raw_total > 0:
                bucket['mapped'] += 1
            else:
                bucket['unmapped'] += 1
            bucket['total_weight'] = float_weight(
                decimal_weight(bucket['total_weight']) + raw_total
            )

    if totals_only:
        return {'items': items}

    heatmap_modules = [
        {
            'module_id': clean(row.get('module_catalogue_id')),
            'moduleId': clean(row.get('module_catalogue_id')),
            'module_name': clean(row.get('title')),
            'moduleName': clean(row.get('title')),
        }
        for row in module_rows
    ]
    heatmap = []
    for item in items:
        module_weights = defaultdict(Decimal)
        module_breakdown = defaultdict(list)
        for mapping in item['mappings']:
            module_id = mapping.get('module_id')
            if not module_id:
                continue
            module_weights[module_id] += decimal_weight(mapping.get('weight'))
            module_breakdown[module_id].append(mapping)
        heatmap.append({
            'ksb_id': item['ksb_id'],
            'ksbId': item['ksb_id'],
            'coverage_key': item['coverage_key'],
            'coverageKey': item['coverage_key'],
            'code': item['code'],
            'title': item['title'],
            'ksb_type': item['ksb_type'],
            'ksbType': item['ksb_type'],
            'source_type': item['source_type'],
            'sourceType': item['source_type'],
            'source_id': item['source_id'],
            'sourceId': item['source_id'],
            'total': item['raw_total_weight'],
            'modules': [
                {
                    **module,
                    'weight': float_weight(module_weights.get(module['module_id'], 0)),
                    'mappings': module_breakdown.get(module['module_id'], []),
                }
                for module in heatmap_modules
            ],
        })

    return {
        'summary': summary,
        'items': items,
        'heatmap': {
            'modules': heatmap_modules,
            'rows': heatmap,
        },
    }


def ksb_sort_key(code):
    text = normalise_code(code)
    prefix_order = {'K': 0, 'S': 1, 'B': 2}
    prefix = prefix_order.get(text[:1], 9)
    numbers = []
    for part in text[1:].replace('-', '.').split('.'):
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    return (prefix, numbers, text)
