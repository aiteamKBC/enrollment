"""Small definition projection for achievement, without the coverage matrix."""
from .ksb_coverage import (
    clean, coverage_identity, coverage_sort_key, normalise_code,
    normalise_required_definition, required_definition_from_mapping,
)


def achievement_definitions(required_ksbs, mapping_rows):
    """Match build_coverage's definitions, including its legacy mapping fallback.

    Achievement needs identity, type and description, not mapping evidence,
    weights or a cell for every KSB/module pair.
    """
    definitions = {}
    for item in required_ksbs:
        definition = normalise_required_definition(item)
        if definition['code']:
            definitions[coverage_identity(definition)] = definition
    if not required_ksbs:
        for row in mapping_rows:
            if not clean(row.get('module_catalogue_id')):
                continue
            mapping = {
                'ksb_id': clean(row.get('ksb_id') or row.get('ksb_code')),
                'code': normalise_code(row.get('ksb_code')),
                'description': clean(row.get('ksb_description')),
                'source_type': clean(row.get('source_type')),
                'source_id': clean(row.get('source_id')),
            }
            identity = coverage_identity(mapping)
            if mapping['code'] and identity not in definitions:
                definitions[identity] = required_definition_from_mapping(mapping)
    return [definitions[key] for key in sorted(definitions, key=coverage_sort_key)]
