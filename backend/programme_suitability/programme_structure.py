"""Canonical Programme Structure snapshots and compatibility projections.

Phase 0/1: this module is additive. Existing programmeModules and
skillsRadarCatalogues remain readable and writable by their current flows.
"""
import copy
import hashlib
import json
from typing import Any

KEY = "programmeStructure"
SCHEMA_VERSION = 2


def _leaves(items):
    for item in items or []:
        if item.get("itemType") == "GROUP" or ("items" in item and item.get("itemType") != "MODULE"):
            yield from _leaves(item.get("items"))
        elif item.get("active", True):
            yield item


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def version(snapshot):
    """Hash semantic content; relationship order is irrelevant, display order is not."""
    value = copy.deepcopy(snapshot)
    value.pop("_draftRevision", None)
    for programme in value.get("programmes", []):
        programme["modules"] = sorted(programme.get("modules", []), key=lambda x: (x.get("order", 0), x["id"]))
        programme["ksbs"] = sorted(programme.get("ksbs", []), key=lambda x: (x.get("code", ""), x["id"]))
        programme["mappings"] = sorted(programme.get("mappings", []), key=lambda x: (x["ksbId"], x["moduleId"], x.get("pathwayId", "")))
        programme["cohorts"] = sorted(programme.get("cohorts", []), key=lambda x: x.get("id", ""))
        for cohort in programme["cohorts"]:
            cohort["modules"] = sorted(cohort.get("modules", []), key=lambda x: (x.get("order", 0), x.get("id", "")))
            cohort["mappings"] = sorted(cohort.get("mappings", []), key=lambda x: (x.get("ksbId", ""), x.get("moduleId", "")))
    payload = {"schemaVersion": value.get("schemaVersion", SCHEMA_VERSION), "programmes": sorted(value.get("programmes", []), key=lambda x: x["id"])}
    if 'intakeCalendar' in value:
        payload['intakeCalendar'] = value['intakeCalendar']
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def from_legacy(modules_config, catalogues):
    """Build an additive canonical snapshot from existing published layout data."""
    programmes = []
    for definition in modules_config.get("programmes", []):
        pid = definition["id"]
        catalogue = (catalogues or {}).get(pid) or {"records": []}
        records = {r["id"]: r for r in catalogue.get("records", [])}
        ksbs, mappings = [], []
        for record in records.values():
            ksbs.append({"id": record["id"], "code": record["code"], "kind": record["kind"], "description": record["description"], "status": "active"})
            keys = record.get("moduleKeys") or ([record["moduleKey"]] if record.get("moduleKey") else [])
            for module_id in keys:
                mappings.append({"programmeId": pid, "ksbId": record["id"], "moduleId": module_id, "status": "active"})
        modules = []
        for module in _leaves(definition.get("modules", [])):
            modules.append({"id": module["id"], "title": module["title"], "description": module.get("description", ""), "order": module.get("order", 0), "active": module.get("active", True), "selection": "required" if module.get("isMandatory") else "optional", "selectable": module.get("selectable", False), "credits": module.get("credits"), "sessions": module.get("sessions")})
        # Existing programme-wide data remains authoritative for legacy learners.
        # The cohort projection is additive and can be edited independently.
        cohort_id = f"{pid}-FEB-2027"
        cohort_modules = copy.deepcopy(modules)
        cohort_mappings = copy.deepcopy(mappings)
        for rel in cohort_mappings:
            rel["cohortModuleId"] = rel.get("moduleId")
        pathways = copy.deepcopy(definition.get("pathways", []))
        routes = [{"id": path.get("id"), "name": path.get("title", path.get("id", "Route")), "modules": copy.deepcopy(path.get("modules", [])), "targetCredits": path.get("targetCredits"), "requiredOptionalModulesCount": path.get("requiredOptionalModulesCount")} for path in pathways] if pid == "PCP_L6" else []
        programmes.append({"id": pid, "title": definition.get("title", pid), "status": "active", "modules": modules, "ksbs": ksbs, "mappings": mappings, "cohorts": [{"id": cohort_id, "name": "Feb 2027", "status": "active", "programmeId": pid, "modules": cohort_modules, "mappings": cohort_mappings, "routes": routes}], "selectionPolicy": {"type": "pcp-pathways" if pid == "PCP_L6" else "required-optional", "pathways": pathways}})
    snapshot = {"schemaVersion": SCHEMA_VERSION, "programmes": programmes}
    snapshot["version"] = version(snapshot)
    return snapshot


def validate_selection(programme, selected_ids):
    """Validate the generic required/optional contract against a canonical programme."""
    modules = {m["id"]: m for m in programme.get("modules", []) if m.get("active", True)}
    selected = set(selected_ids or [])
    required = {m["id"] for m in modules.values() if m.get("selection") == "required"}
    optional = {m["id"] for m in modules.values() if m.get("selection") == "optional"}
    if not selected.issubset(optional | required):
        raise ValueError("Only active eligible modules may be selected.")
    if not required.issubset(selected):
        raise ValueError("Required modules are automatically included.")
    policy = programme.get("selectionPolicy") or {}
    if policy.get("minOptional") is not None and len(selected - required) < policy["minOptional"]:
        raise ValueError("The minimum optional module count has not been met.")
    if policy.get("maxOptional") is not None and len(selected - required) > policy["maxOptional"]:
        raise ValueError("The maximum optional module count has been exceeded.")
    return sorted(selected)


def applicable_ksbs(programme, selected_ids):
    selected = set(validate_selection(programme, selected_ids))
    ids = {m["ksbId"] for m in programme.get("mappings", []) if m.get("moduleId") in selected and m.get("status", "active") == "active"}
    return [k for k in programme.get("ksbs", []) if k.get("id") in ids and k.get("status", "active") == "active"]


def project_modules(snapshot):
    """Compatibility projection for the existing programmeModules consumer."""
    return {"schemaVersion": 2, "programmes": [{"id": p["id"], "title": p["title"], "modules": copy.deepcopy(p.get("modules", [])), "pathways": copy.deepcopy((p.get("selectionPolicy") or {}).get("pathways", []))} for p in snapshot.get("programmes", [])]}

def project_catalogues(snapshot):
    """Compatibility projection for existing Skills Radar catalogue consumers."""
    out = {}
    for p in snapshot.get("programmes", []):
        by_id = {k["id"]: k for k in p.get("ksbs", [])}
        modules = {}
        for mapping in p.get("mappings", []): modules.setdefault(mapping["ksbId"], []).append(mapping["moduleId"])
        records = []
        for ksb in by_id.values():
            keys = sorted(set(modules.get(ksb["id"], [])))
            if keys: records.append({"id": ksb["id"], "code": ksb["code"], "kind": ksb["kind"], "description": ksb["description"], "moduleKey": keys[0], **({"moduleKeys": keys} if len(keys) > 1 else {})})
        out[p["id"]] = {"programmeId": p["id"], "records": records, "version": version({"schemaVersion": 1, "programmes": [p]})}
    return out


def resolve_published(layout, *, compare_legacy=True):
    """Resolve canonical structure without changing existing consumers.

    If a published snapshot exists it is returned. Otherwise a compatibility
    snapshot is derived from the legacy layout. Differences are reported, never
    silently applied.
    """
    layout = layout or {}
    canonical = layout.get(KEY)
    if isinstance(canonical, dict) and isinstance(canonical.get("programmes"), list):
        snapshot = copy.deepcopy(canonical)
        # Older canonical snapshots predate cohort scoping. Keep their
        # programme-wide projections intact and expose a derived Feb 2027
        # cohort only as an additive compatibility view.
        for programme in snapshot.get("programmes", []):
            if "cohorts" not in programme:
                pid = programme.get("id", "programme")
                programme["cohorts"] = [{"id": f"{pid}-FEB-2027", "name": "Feb 2027", "status": "active", "programmeId": pid, "modules": copy.deepcopy(programme.get("modules", [])), "mappings": copy.deepcopy(programme.get("mappings", []))}]
        snapshot.setdefault("version", version(snapshot))
        return {"source": "canonical", "snapshot": snapshot, "differences": []}
    modules = layout.get("programmeModules") or {"programmes": []}
    catalogues = layout.get("skillsRadarCatalogues") or {}
    snapshot = from_legacy(modules, catalogues)
    return {"source": "legacy", "snapshot": snapshot, "differences": legacy_differences(snapshot, modules, catalogues) if compare_legacy else []}


def resolve_assigned_cohort(snapshot, programme_id, cohort_id):
    """Resolve only an administrator-assigned cohort ID for a programme.

    Cohort display names are deliberately ignored.  A missing or mismatched
    assignment is blocked rather than falling back to Feb 2027 or another
    cohort.
    """
    if not programme_id or not cohort_id:
        return None
    programme = next((p for p in snapshot.get("programmes", []) if p.get("id") == programme_id), None)
    if not programme:
        return None
    return next((c for c in programme.get("cohorts", []) if c.get("id") == cohort_id and c.get("programmeId", programme_id) == programme_id), None)


def learner_published_layout(kind, learner_id, *, module_version=None):
    """Read the learner's immutable layout pin before considering the latest.

    Wizard Layout assignments take precedence over the older modules pin. A
    missing pinned publication is an error, never permission to use the latest.
    This is read-only; neither the assignment nor the assessment is changed.
    """
    from django.db import connections
    from enrolment_api.wizard_layout import CONN, ASSIGNMENT_TABLE, read_current, read_version

    with connections[CONN].cursor() as cursor:
        cursor.execute(
            f'SELECT "Override_version", "Submitted_version", "Pinned_version" '
            f'FROM enrolment."{ASSIGNMENT_TABLE}" '
            'WHERE "Learner_kind" = %s AND "Learner_id" = %s',
            [kind, learner_id],
        )
        assignment = cursor.fetchone()
        layout_pin = next((value for value in assignment if value is not None), None) if assignment else None
        requested = layout_pin if layout_pin is not None else module_version
        if requested == 0:
            # Zero is the legacy modules configuration identity, not a request
            # to upgrade the learner to the latest canonical publication.
            return 0, None, 'legacy'
        if requested is not None and requested != 0:
            published = read_version(cursor, requested)
            if published is None:
                raise LookupError('The learner\'s saved published layout is unavailable.')
            return published[0], published[1], 'layout-pin' if layout_pin is not None else 'modules-pin'
        current = read_current(cursor)
        return (current[0], current[1], 'latest') if current else (None, None, 'missing')


def legacy_differences(snapshot, modules_config, catalogues):
    """Report projection mismatches without mutating either representation."""
    differences = []
    projected_modules = project_modules(snapshot)
    projected_catalogues = project_catalogues(snapshot)
    if projected_modules != modules_config:
        differences.append("programmeModules projection differs")
    for pid, catalogue in (catalogues or {}).items():
        if pid in projected_catalogues:
            left = [(r.get("id"), r.get("code"), r.get("kind"), r.get("description"), tuple(sorted(r.get("moduleKeys") or ([r["moduleKey"]] if r.get("moduleKey") else [])))) for r in catalogue.get("records", [])]
            right = [(r.get("id"), r.get("code"), r.get("kind"), r.get("description"), tuple(sorted(r.get("moduleKeys") or ([r["moduleKey"]] if r.get("moduleKey") else [])))) for r in projected_catalogues[pid].get("records", [])]
            if sorted(left) != sorted(right): differences.append(f"skillsRadarCatalogues[{pid}] projection differs")
    return differences


def validate_structure(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("programmes"), list): raise ValueError("Provide a valid Programme Structure.")
    if "intakeCalendar" in snapshot:
        from .intake_calendar import validate_calendar
        validate_calendar(snapshot["intakeCalendar"])
    seen_programmes = set()
    for p in snapshot["programmes"]:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"].strip(): raise ValueError("Every programme needs a stable ID.")
        if p["id"] in seen_programmes: raise ValueError(f"Duplicate programme ID: {p['id']}.")
        seen_programmes.add(p["id"])
        if not isinstance(p.get("title"), str) or not p["title"].strip(): raise ValueError("Every programme needs a name.")
        modules = p.get("modules") or []; ksbs = p.get("ksbs") or []; mappings = p.get("mappings") or []
        mids = set(); kids = set(); codes = set()
        for m in modules:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str) or m["id"] in mids: raise ValueError("Module IDs must be unique within a programme.")
            mids.add(m["id"]);
            if not str(m.get("title", "")).strip(): raise ValueError("Every module needs a name.")
            if m.get("selection") not in {"required", "optional"}: raise ValueError("Modules must be Required or Optional.")
        for k in ksbs:
            if not isinstance(k, dict) or not isinstance(k.get("id"), str) or k["id"] in kids: raise ValueError("KSB IDs must be unique within a programme.")
            kids.add(k["id"]); code = str(k.get("code", "")).strip()
            if not code or code in codes: raise ValueError(f"Duplicate KSB code: {code}.")
            codes.add(code)
            if k.get("kind") not in {"Knowledge", "Skill", "Behaviour"} or not str(k.get("description", "")).strip(): raise ValueError(f"KSB {code} has invalid metadata.")
        relationships = set()
        for rel in mappings:
            if rel.get("ksbId") not in kids or rel.get("moduleId") not in mids: raise ValueError("Mappings must reference an existing KSB and module.")
            key = (rel["ksbId"], rel["moduleId"], rel.get("pathwayId"))
            if key in relationships: raise ValueError("Duplicate Module?KSB relationship.")
            relationships.add(key)
        cohort_ids = set()
        windows = []
        for cohort in p.get("cohorts", []) or []:
            if not isinstance(cohort, dict) or not isinstance(cohort.get("id"), str) or not cohort["id"].strip():
                raise ValueError("Every cohort needs a stable ID.")
            if cohort["id"] in cohort_ids: raise ValueError("Cohort IDs must be unique within a programme.")
            cohort_ids.add(cohort["id"])
            if not str(cohort.get("name", "")).strip(): raise ValueError("Every cohort needs a name.")
            if cohort.get('programmeId', p['id']) != p['id']:
                raise ValueError('A Cohort must belong to its containing Programme.')
            from .intake_calendar import cohort_assignment_window
            window = cohort_assignment_window(cohort)
            if window and cohort.get('status', 'active') not in {'archived', 'deleted', 'inactive'}:
                if any(window[0] <= other[1] and other[0] <= window[1] for other in windows):
                    raise ValueError('Cohort assignment periods overlap within this Programme.')
                windows.append(window)
            cmids = set()
            for module in cohort.get("modules", []) or []:
                if not isinstance(module, dict) or not isinstance(module.get("id"), str) or module["id"] in cmids:
                    raise ValueError("Cohort module IDs must be unique within a cohort.")
                cmids.add(module["id"])
                if module.get("selection") not in {"required", "optional"}: raise ValueError("Cohort modules must be Required or Optional.")
            for rel in cohort.get("mappings", []) or []:
                if rel.get("ksbId") not in kids or rel.get("moduleId") not in cmids:
                    raise ValueError("Cohort mappings must reference an existing cohort KSB and module.")
            route_ids = set()
            for route in cohort.get("routes", []) or []:
                if not isinstance(route, dict) or not isinstance(route.get("id"), str) or not route["id"].strip():
                    raise ValueError("Every PCP route needs a stable ID.")
                if route["id"] in route_ids: raise ValueError("PCP route IDs must be unique within a cohort.")
                route_ids.add(route["id"])
                if not str(route.get("name", "")).strip(): raise ValueError("Every PCP route needs a name.")
                for module in route.get("modules", []) or []:
                    if not isinstance(module, dict) or not isinstance(module.get("id"), str):
                        raise ValueError("PCP route modules need stable IDs.")
                    if module.get("selection") not in {"required", "optional", None} and not isinstance(module.get("isMandatory"), bool):
                        raise ValueError("PCP route module selection is invalid.")
    return snapshot


def resolve_explicit_layout(layout, requested_version, *, layout_version=None):
    """Shadow resolver: never substitutes a newer layout for a requested version."""
    if not isinstance(requested_version, int) or isinstance(requested_version, bool) or requested_version < 1:
        raise ValueError("An explicit published layout version is required.")
    if layout_version != requested_version:
        raise LookupError("The requested published layout version is unavailable.")
    return resolve_published(layout, compare_legacy=True)


def semantic_parity(legacy_modules, legacy_catalogue, snapshot, programme_id):
    """Compare canonical shadow content with a legacy programme projection."""
    differences = []
    canonical = next((p for p in snapshot.get("programmes", []) if p.get("id") == programme_id), None)
    if canonical is None: return ["programme missing from canonical snapshot"]
    legacy_def = next((p for p in legacy_modules.get("programmes", []) if p.get("id") == programme_id), None)
    if legacy_def is None: return ["programme missing from legacy modules"]
    legacy_modules_by_id = {m["id"]: m for m in _leaves(legacy_def.get("modules", []))}
    can_modules = {m["id"]: m for m in canonical.get("modules", [])}
    if set(legacy_modules_by_id) != set(can_modules): differences.append("module identities differ")
    for mid in set(legacy_modules_by_id) & set(can_modules):
        old, new = legacy_modules_by_id[mid], can_modules[mid]
        if old.get("title") != new.get("title") or old.get("order") != new.get("order"): differences.append(f"module {mid} metadata/order differs")
        expected = "required" if old.get("isMandatory") else "optional"
        if new.get("selection") != expected: differences.append(f"module {mid} selection differs")
    old_records = {r["id"]: r for r in legacy_catalogue.get("records", [])}
    new_records = {k["id"]: k for k in canonical.get("ksbs", [])}
    if set(old_records) != set(new_records): differences.append("KSB identities differ")
    for kid in set(old_records) & set(new_records):
        old, new = old_records[kid], new_records[kid]
        if (old.get("code"), old.get("kind"), old.get("description")) != (new.get("code"), new.get("kind"), new.get("description")): differences.append(f"KSB {kid} metadata differs")
    return differences


def resolve_pcp_shadow(snapshot, programme_id="PCP_L6"):
    """Explicit PCP compatibility delegation; no fabricated direct mappings."""
    programme = next((p for p in snapshot.get("programmes", []) if p.get("id") == programme_id), None)
    if not programme or (programme.get("selectionPolicy") or {}).get("type") != "pcp-pathways":
        raise ValueError("PCP shadow snapshot is missing pathway allocation metadata.")
    return {"mode": "legacy-pcp-allocation", "programme": programme_id, "snapshotVersion": snapshot.get("version"), "pathways": programme["selectionPolicy"].get("pathways", []), "ksbs": programme.get("ksbs", [])}


def validate_pcp_shadow_selection(snapshot, raw, *, version=None):
    """Run the exact legacy PCP validator against a canonical snapshot.

    PCP pathway/owner allocation is intentionally delegated; this helper only
    supplies the published pathway configuration and returns the legacy
    canonical selection result for parity comparisons.
    """
    resolved = resolve_pcp_shadow(snapshot)
    from .modules_service import validate_snapshot_choices
    from .modules_configuration import defaults
    config = defaults()
    pcp = next(p for p in config['programmes'] if p['id'] == resolved['programme'])
    pcp['pathways'] = copy.deepcopy(resolved['pathways'])
    expected_version = version if version is not None else snapshot.get("version")
    return validate_snapshot_choices(raw, expected_version, config, complete=False)


def shadow_version_context(*, layout_version, programme_structure_version=None, programme_modules_version=None, skills_radar_versions=None, available_versions=None, source_identities=None):
    """Read-only consistency contract for future unified learner resolution."""
    pins = [v for v in (layout_version, programme_structure_version, programme_modules_version) if v is not None]
    radar = [v for v in (skills_radar_versions or {}).values() if v is not None]
    requested = set(pins + radar)
    missing = sorted((v for v in requested if available_versions is not None and v not in set(available_versions)), key=str)
    identities = source_identities or {}
    identity_values = {value for value in identities.values() if value is not None}
    # Independent version counters are compatible when they resolve to the
    # same published source identity; numeric equality alone is insufficient.
    conflicts = (sorted(requested, key=str) if len(requested) > 1 else []) if not identities else (sorted(requested, key=str) if len(identity_values) > 1 else [])
    return {"status": "missing" if missing else ("conflict" if conflicts else "consistent"), "layoutVersion": layout_version, "requestedVersions": sorted(requested, key=str), "missingVersions": missing, "conflicts": conflicts, "answersMayBeReadOnly": True}


def shadow_compare_learner(layout, programme_id, selected_module_ids=None, *, layout_version=None, module_version=None, catalogue_version=None):
    """Read-only learner parity report; never reads drafts or writes pins."""
    resolved = resolve_published(layout, compare_legacy=True)
    programme = next((p for p in resolved['snapshot'].get('programmes', []) if p.get('id') == programme_id), None)
    if not programme:
        return {'programme': programme_id, 'status': 'BLOCKED', 'reasons': ['Programme is absent from the published snapshot.']}
    # All canonical projections in one published Wizard Layout share the
    # layout snapshot identity. Their independent numeric/content hashes are
    # compared as content, never as if they were the same counter.
    source = layout.get('_layoutIdentity') or (f'layout:{layout_version}' if layout_version is not None else None)
    identities = {'layout': source, 'structure': source, 'modules': source, 'catalogue': source}
    context = shadow_version_context(layout_version=layout_version, programme_structure_version=resolved['snapshot'].get('version'), programme_modules_version=module_version, skills_radar_versions={programme_id: catalogue_version} if catalogue_version is not None else {}, source_identities=identities)
    if programme_id == 'PCP_L6':
        return {'programme': programme_id, 'status': 'BLOCKED' if context['status'] != 'consistent' else 'MATCH', 'mode': 'legacy-pcp-allocation', 'versionConsistency': context, 'pcp': resolve_pcp_shadow(resolved['snapshot'])}
    selected = selected_module_ids or [m['id'] for m in programme.get('modules', []) if m.get('selection') == 'required']
    try:
        ksbs = applicable_ksbs(programme, selected)
        return {'programme': programme_id, 'status': 'MATCH' if context['status'] == 'consistent' else 'BLOCKED', 'versionConsistency': context, 'moduleIds': sorted(selected), 'ksbIds': sorted(k['id'] for k in ksbs), 'ksbCount': len(ksbs), 'differences': resolved.get('differences', [])}
    except (ValueError, TypeError) as exc:
        return {'programme': programme_id, 'status': 'MISMATCH', 'versionConsistency': context, 'differences': [str(exc)]}
