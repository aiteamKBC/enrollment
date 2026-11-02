"""Explicit item types, with structural compatibility for older publications."""
import copy


def is_group(item):
    if 'itemType' in item:
        return item['itemType'] == 'GROUP'
    return isinstance(item.get('items'), list)


def corrected_pmo_group(configuration):
    """Correct only the known group metadata; retain children and historical input."""
    result = copy.deepcopy(configuration)
    for programme in result.get('programmes', []):
        if programme.get('id') != 'PCP_L6':
            continue
        for path in programme.get('pathways', []):
            if path.get('id') != 'chartered':
                continue
            for item in path.get('modules', []):
                if item.get('id') == 'certified-pmo':
                    item['itemType'] = 'GROUP'
                    for field in ('sessions', 'credits', 'selectable', 'isMandatory'):
                        item.pop(field, None)
    return result
