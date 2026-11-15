"""Explicit, content-preserving recovery of a generic draft's publication base.

This prepares data only. It never changes publication selection or writes data.
"""
import copy

from .publication import build_published_layout, preflight


def prepare_scope_rebase(original, current, draft):
    """Keep the saved draft verbatim; validate it against the target publication.

    ``original`` and ``current`` are full read_version() results, including scope,
    kind and archive metadata. Only generic -> apprenticeship recovery is allowed.
    This is deliberately not a three-way academic-content merge.
    """
    from .draft_rebase import RebaseConflict
    from enrolment_api.wizard_layout import normalize_layout

    if (len(original) < 9 or len(current) < 9
            or (original[4] or 'default') != 'default' or original[7] is not None or original[8]
            or (current[4] or 'default') != 'default' or current[7] != 'apprenticeship' or current[8]
            or original[0] == current[0]):
        raise RebaseConflict('Scope recovery requires an active generic source and an active apprenticeship target.')
    if not isinstance(original[1].get('programmeStructure'), dict):
        raise RebaseConflict('The generic source has no canonical Programme Structure.')
    if current[1].get('programmeStructure') is not None:
        raise RebaseConflict('The apprenticeship target already has canonical data; review its content conflicts separately.')
    proposed = copy.deepcopy(draft)
    result = preflight({'programmeStructure': proposed}, current_version=current[0], base_version=current[0])
    built = build_published_layout(current[1], result)
    normalized, _ = normalize_layout(copy.deepcopy(built), copy.deepcopy(current[1]))
    if normalized.get('programmeStructure') != proposed:
        raise RebaseConflict('Publication normalization would change the saved Programme Structure.')
    # Scope recovery cannot silently import generic wizard content, replace
    # apprenticeship content, or introduce calendar/projection changes.
    if ({k: v for k, v in normalized.items() if k != 'programmeStructure'}
            != {k: v for k, v in current[1].items() if k != 'programmeStructure'}):
        raise RebaseConflict('Scope recovery would change unrelated apprenticeship publication content.')
    return proposed
