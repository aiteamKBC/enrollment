"""Working migrated definitions; callers lock the overlay before synchronizing.

Answers stay in their authoritative JSON store. Retired field metadata records
the identity/type of saved answers that no longer belong to the active form.
Neither progress nor meeting intelligence is written by this module.
"""
from copy import deepcopy

from django.db import connections, transaction
from django.utils import timezone

from .migrated_reviews import EDITABLE_STATUSES, all_fields, validate_answers, _visible_fields
from .migrated_templates import FAMILIES, fingerprint, resolve_template, snapshot_for, validate_managed_definition
from .migrated_signature_rules import requirements_for_definition


class TemplateSyncConflict(ValueError):
    def __init__(self, message, *, fields=(), code="template_sync_conflict"):
        super().__init__(message)
        self.fields, self.code = list(fields), code

    def response(self):
        return {"status": "conflict", "upToDate": False, "code": self.code,
                "message": str(self), "fields": self.fields}


def lock_template_family(family):
    """Serialize resolution with template edits/activation, including new overrides.

    Row locks alone cannot protect an absent Programme override. The management
    endpoints take this same transaction-scoped lock before locking templates.
    SQLite is used only for hermetic tests; PostgreSQL exercises real races.
    """
    if family not in FAMILIES:
        raise TemplateSyncConflict("This migrated review family cannot be synchronized.")
    connection = connections["default"]
    if not connection.in_atomic_block:
        raise RuntimeError("Template resolution locks require a transaction.")
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [190702013, FAMILIES.index(family) + 1])


def snapshot_fingerprint(snapshot):
    # Exclude bookkeeping and retained field history from the definition version.
    return fingerprint({key: snapshot.get(key) for key in ("sections", "name", "templateSource", "otjh", "signatures")})


def snapshot_state(overlay):
    snapshot = overlay.template_snapshot or {}
    frozen = overlay.status not in EDITABLE_STATUSES
    # Frozen/participant reads intentionally do not compare with a master.
    return {"status": "frozen" if frozen else "working", "upToDate": None,
            "fingerprint": snapshot_fingerprint(snapshot),
            "synchronizedAt": (snapshot.get("templateSync") or {}).get("synchronizedAt"),
            "source": deepcopy(snapshot.get("templateSource") or {})}


def active_answers(snapshot, answers):
    """Only visible current fields may validate or appear in a signed PDF."""
    return {field["key"]: answers[field["key"]] for field in _visible_fields(snapshot, answers)
            if field.get("aptemType") != 11 and field["key"] in answers}


def preserve_inactive_answers(snapshot, previous, incoming):
    """Incoming keys are strictly validated separately; preserve stored retired work."""
    active = {field["key"] for field in _visible_fields(snapshot, incoming) if field.get("aptemType") != 11}
    return {**{key: value for key, value in previous.items() if key not in active}, **incoming}


def merge_snapshot(previous, answers, template, family):
    """Pure merge by exact field key. A regenerated key is a new field, never guessed."""
    if template.review_family != family or (previous.get("templateSource") or {}).get("family", family) != family:
        raise TemplateSyncConflict("The resolved template does not match this review family.")
    try:
        validate_managed_definition(template.definition_json, family=family)
    except ValueError as exc:
        raise TemplateSyncConflict("The resolved migrated template is invalid. Ask a template administrator to correct it.") from exc
    latest = snapshot_for(template)
    old_fields = {field["key"]: field for field in all_fields(previous)}
    new_fields = {field["key"]: field for field in all_fields(latest)}
    retired = deepcopy((previous.get("templateSync") or {}).get("retiredFields") or {})
    conflicts = []
    for key, field in new_fields.items():
        old = old_fields.get(key) or retired.get(key)
        if key not in answers or answers[key] in (None, ""):
            continue
        if old is None:
            conflicts.append(key)
            continue
        compatible = old.get("aptemType") == field["aptemType"] or {old.get("aptemType"), field["aptemType"]} <= {1, 13}
        if not compatible:
            conflicts.append(key)
            continue
        standalone = {**field, "ifTrue": [], "ifFalse": []}
        try:
            validate_answers({"sections": [{"key": "validation", "fields": [standalone]}]}, {key: answers[key]})
        except ValueError:
            conflicts.append(key)
    if conflicts:
        raise TemplateSyncConflict(
            "This review template has changed and cannot be safely synchronized because one or more answered fields changed type or allowed values. Your saved answers are unchanged. Ask a template administrator to correct the affected fields.",
            fields=conflicts,
        )
    for key, field in old_fields.items():
        if key not in new_fields and key in answers:
            retired[key] = {k: deepcopy(v) for k, v in field.items() if k not in {"ifTrue", "ifFalse"}}
    for key in new_fields:
        retired.pop(key, None)
    latest["templateSync"] = {"fingerprint": snapshot_fingerprint(latest),
                              "synchronizedAt": timezone.now().isoformat(), "retiredFields": retired}
    return latest


def synchronize_locked(overlay, family, programme_key):
    """Caller holds the overlay lock until commit; never save a stale overlay."""
    if overlay.status not in EDITABLE_STATUSES:
        return False
    lock_template_family(family)
    template = resolve_template(programme_key, family, lock=True)
    if template is None:
        raise TemplateSyncConflict("No active migrated template is available for this review family. Your saved work is unchanged. Ask a template administrator to configure one.", code="template_sync_missing")
    if overlay.migrated_template_id == template.pk and snapshot_fingerprint(overlay.template_snapshot) == snapshot_fingerprint(snapshot_for(template)):
        return False
    latest = merge_snapshot(overlay.template_snapshot, overlay.answers or {}, template, family)
    update_fields = ["template_snapshot", "migrated_template", "updated_at"]
    if "signatures" in latest or "signatures" in overlay.template_snapshot:
        # Unrelated edits to legacy templates must not reset stored rules.
        # Removing explicit settings restores the family defaults.
        overlay.signature_requirements = requirements_for_definition(latest, family)
        update_fields.append("signature_requirements")
    overlay.template_snapshot = latest
    overlay.migrated_template = template
    overlay.save(update_fields=update_fields)
    return True


def synchronize_definition_locked(overlay, definition):
    from .migrated_reviews import review_family
    return synchronize_locked(overlay, review_family((definition.get("historicalReview") or {}).get("type")), definition.get("migratedProgrammeKey"))


def synchronize_on_open(owner, review_id, definition):
    """Assigned-coach entry point only. All other definition readers stay read-only."""
    from .views import _imported_review_definition, _owned_migrated_overlay
    if not definition.get("migratedForm") or definition.get("localStatus") not in EDITABLE_STATUSES:
        return definition
    with transaction.atomic():
        overlay = _owned_migrated_overlay(owner, definition, lock=True)
        if overlay is None:
            return None
        current = _imported_review_definition(owner, review_id)
        if not current or not current.get("migratedForm"):
            return current
        try:
            changed = synchronize_definition_locked(overlay, current)
        except TemplateSyncConflict as exc:
            current["templateSync"] = {**snapshot_state(overlay), **exc.response()}
            current["readOnly"] = True
            current["canCalculateProgress"] = False
            return current
        result = _imported_review_definition(owner, review_id) if changed else current
        result["templateSync"] = {**snapshot_state(overlay), "upToDate": True}
        return result
