"""Programme totals from the pinned Training Plan and recorded RPL reductions."""

from decimal import Decimal, InvalidOperation


def training_hours_summary(planned_hours, reductions):
    try:
        original = Decimal(str(planned_hours))
    except InvalidOperation:
        original = None
    if original is not None and (not original.is_finite() or original < 0):
        original = None
    reduction = sum((Decimal(str(hours)) for hours in reductions), Decimal('0'))
    return {
        'originalHours': original,
        'reductionHours': reduction,
        'remainingHours': original - reduction if original is not None else None,
    }
