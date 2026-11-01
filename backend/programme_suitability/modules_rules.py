"""PCP rules apply to published snapshots, with no rules inferred from row order."""
from decimal import Decimal

from .configuration import ConfigurationError


def available_modules(items):
    out = []
    for item in items:
        if not item.get('active', True):
            continue
        if item.get('selectable'):
            out.append(item)
        out.extend(available_modules(item.get('items', [])))
    return out


def credits(items):
    return sum((Decimal(str(item.get('credits', 0))) for item in items), Decimal(0))


def validate_pathway_rules(path):
    count = path.get('requiredOptionalModulesCount')
    if count is None:
        return  # Historical version 1 configurations retain their original rules.
    modules = available_modules(path['modules'])
    mandatory = [m for m in modules if m.get('isMandatory', False)]
    optional = [m for m in modules if not m.get('isMandatory', False)]
    if (count > 0 and not path.get('allowModuleSelection')) or count > len(optional):
        raise ConfigurationError('Required optional module count exceeds the available selectable optional modules.')
    if path.get('targetCredits') is None:
        raise ConfigurationError('Set target credits for a pathway with optional selection requirements.')
    remaining = Decimal(str(path['targetCredits'])) - credits(mandatory)
    if remaining < 0:
        raise ConfigurationError('Mandatory credits exceed target credits.')
    # Exact-count subset sums; a guard rejects unusually complex administrator
    # configurations instead of allowing unbounded combination work.
    sums = [set() for _ in range(count + 1)]
    sums[0].add(Decimal(0))
    for module in optional:
        value = Decimal(str(module.get('credits', 0)))
        for size in range(count, 0, -1):
            sums[size].update(s + value for s in sums[size - 1] if s + value <= remaining)
        if sum(map(len, sums)) > 100000:
            raise ConfigurationError('Optional credit combinations are too complex to validate. Simplify the credit values.')
    if remaining not in sums[count]:
        raise ConfigurationError('The required optional module count cannot produce the target credits.')


def validate_active_selection(path, ids, complete=False):
    modules = available_modules(path['modules'])
    by_id = {m['id']: m for m in modules}
    mandatory = {m['id'] for m in modules if m.get('isMandatory', False)}
    if not mandatory <= set(ids):
        raise ConfigurationError('All mandatory modules must remain selected.')
    count = path.get('requiredOptionalModulesCount')
    if count is None:
        if complete:
            raise ConfigurationError('Publish the pathway optional selection requirements before completing module selection.')
        return
    optional_count = len(set(ids) - mandatory)
    if optional_count > count:
        raise ConfigurationError(f'Select exactly {count} optional modules; additional selections are not allowed.')
    total = credits([by_id[mid] for mid in ids])
    target = Decimal(str(path['targetCredits']))
    if total > target or ((complete or optional_count == count) and total != target):
        raise ConfigurationError(f'Selected modules must achieve exactly {target} credits.')
    if complete and optional_count != count:
        raise ConfigurationError(f'Select exactly {count} optional modules before continuing.')
