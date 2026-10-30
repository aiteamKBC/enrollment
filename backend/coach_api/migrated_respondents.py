"""Answer permissions for Aptem continuation snapshots, never source records."""
from .migrated_reviews import EDITABLE_STATUSES, _visible_fields, all_fields, validate_answers
from .migrated_template_sync import active_answers


def writable_keys(overlay, role):
    if role not in {"participant", "employer"}:
        return set()
    snapshot = overlay.template_snapshot
    family = (snapshot.get("templateSource") or {}).get("family") or getattr(getattr(overlay, "migrated_template", None), "review_family", None)
    if role == "employer" and family not in {"PR", "PR_SKILLS_RADAR"}:
        return set()
    return {field["key"] for field in all_fields(snapshot)
            if field.get("aptemType") != 11 and role in (field.get("respondentRoles") or [])}


def merge_party_answers(overlay, incoming, role):
    """Caller holds the overlay lock and has verified ownership and version.

    Merge before evaluating conditions: a respondent can answer a child whose
    parent was answered by the coach, but cannot write a hidden child. Retain
    inactive and retired answers exactly as stored, for historical continuity.
    """
    if overlay.status not in EDITABLE_STATUSES:
        raise ValueError("Submitted review answers cannot be changed after the signature step begins.")
    if not isinstance(incoming, dict):
        raise ValueError("answers must be an object keyed by field id.")
    if not incoming or set(incoming) - writable_keys(overlay, role):
        raise PermissionError("You can only answer questions assigned to your role.")
    merged = {**(overlay.answers or {}), **incoming}
    visible = {field["key"] for field in _visible_fields(overlay.template_snapshot, merged)}
    if set(incoming) - visible:
        raise PermissionError("You cannot answer a hidden conditional question.")
    validate_answers(overlay.template_snapshot, active_answers(overlay.template_snapshot, merged))
    return merged
