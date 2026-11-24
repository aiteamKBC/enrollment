from copy import deepcopy
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal


PENNY = Decimal('0.01')
ALLOCATION = (('kbc_training', 40), ('distance_training', 40), ('assessment', 15), ('materials', 5))
AGREEMENT_LINES = {
    'kbc_training': 'Training that the provider will deliver to the Apprentice Off the Job',
    'distance_training': 'Distance, online or blended learning relating to the off-the-job training',
    'assessment': 'Assessment that the provider will deliver (including Progress Reviews)',
    'materials': 'Materials (non-capital items) used in the delivery of the apprenticeship',
    'epa': 'End Point Assessment',
}


def money(value):
    return format(value, '.2f') if value is not None else None


def _allocations(base):
    exact = {key: base * percent / 100 for key, percent in ALLOCATION}
    rounded = {key: value.quantize(PENNY, rounding=ROUND_DOWN) for key, value in exact.items()}
    training = (base * Decimal('0.80')).quantize(PENNY, rounding=ROUND_HALF_UP)
    for keys, subtotal in ((('kbc_training', 'distance_training'), training),
                           (('assessment', 'materials'), base - training)):
        remaining = int((subtotal - sum(rounded[key] for key in keys)) / PENNY)
        order = sorted(keys, key=lambda key: exact[key] - rounded[key], reverse=True)
        for key in order[:remaining]:
            rounded[key] += PENNY
    return rounded


def _rate(value, units):
    return money((value / units).quantize(PENNY, rounding=ROUND_HALF_UP)) if units else None


def price_programme(programme, group=None, module_revisions=(), *, adjusted_total=None):
    """Build a JSON-safe quote for one apprentice; persist a copy when issuing it."""
    total = programme.total_cost if adjusted_total is None else adjusted_total
    epa = programme.epa_cost
    if (not isinstance(total, Decimal) or not total.is_finite()
            or total < epa or total != total.quantize(PENNY)):
        raise ValueError('The price must cover EPA and be stated to the penny.')
    base = total - epa
    lines = _allocations(base)
    training = lines['kbc_training'] + lines['distance_training']
    module_revisions = tuple(module_revisions) if group is not None else ()
    counts = {}
    for key in ('lectures', 'reviews', 'books'):
        values = [getattr(revision, key) for revision in module_revisions]
        counts[key] = sum(values) if values and None not in values else None
    hours = counts['lectures'] * programme.lecture_hours if counts['lectures'] is not None else None
    lines['epa'] = epa
    return {
        'policy_version': 1,
        'currency': 'GBP',
        'programme_id': str(programme.pk),
        'programme_code': programme.code,
        'programme_updated_at': programme.updated_at.isoformat() if programme.updated_at else None,
        'group_id': str(group.pk) if group else None,
        'module_ids': [str(revision.module_id) for revision in module_revisions],
        'module_revision_ids': [str(revision.pk) for revision in module_revisions],
        'master_total': money(programme.total_cost),
        'total_cost': money(total),
        'base_cost': money(base),
        'total_training': money(training),
        'funding_band_maximum': money(programme.funding_band_maximum),
        'lecture_hours': money(programme.lecture_hours),
        'training_hours': money(hours),
        'counts': counts,
        'allocations': {key: money(value) for key, value in lines.items()},
        'rates': {
            'training_hour': _rate(training, hours),
            'review': _rate(lines['assessment'], counts['reviews']),
            'book': _rate(lines['materials'], counts['books']),
        },
    }


def written_agreement_costs(quote):
    from learner_api.written_agreement import COST_ITEMS

    prices = {label: float(quote['allocations'][key]) for key, label in AGREEMENT_LINES.items()}
    funding_cap = quote['funding_band_maximum']
    return {
        'items': [{'item': label, 'price': prices.get(label)} for label in COST_ITEMS],
        'total': float(quote['total_cost']),
        'fundingBandMaximum': float(funding_cap) if funding_cap is not None else None,
        'balanceDue': None,
        'pricingSnapshot': deepcopy(quote),
    }
