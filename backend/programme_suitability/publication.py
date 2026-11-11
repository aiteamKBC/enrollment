"""Gated Programme Structure publication preflight and transaction boundary.

This module deliberately has no learner-facing callers yet.  It prepares the
existing Wizard Layout publisher for a future, explicitly enabled rollout.
"""
import copy
from django.conf import settings

from .programme_structure import (
    KEY, project_catalogues, project_modules, resolve_pcp_shadow,
    validate_structure, version, from_legacy,
)

SUPPORTED = {"ME_L4", "MM_L6", "APM_L4", "PCP_L6"}


def compare_structures(published, proposed):
    """Return named, identity-aware changes; relationship order is irrelevant."""
    changes = []
    old_by = {p['id']: p for p in (published or {}).get('programmes', [])}
    new_by = {p['id']: p for p in (proposed or {}).get('programmes', [])}
    for pid in sorted(set(new_by) - set(old_by)):
        changes.append({'programmeId': pid, 'programmeName': new_by[pid].get('title', pid), 'type': 'programme-added'})
    for pid in sorted(set(old_by) - set(new_by)):
        changes.append({'programmeId': pid, 'programmeName': old_by[pid].get('title', pid), 'type': 'programme-removed'})
    for pid in sorted(set(old_by) & set(new_by)):
        old, new = old_by[pid], new_by[pid]
        old_cohorts = {c['id']: c for c in old.get('cohorts', [])}
        new_cohorts = {c['id']: c for c in new.get('cohorts', [])}
        for cid in sorted(set(old_cohorts) | set(new_cohorts)):
            before, after = old_cohorts.get(cid), new_cohorts.get(cid)
            if before != after:
                changes.append({'programmeId': pid, 'cohortId': cid,
                    'cohortName': (after or before).get('name', ''),
                    'type': 'cohort-added' if before is None else 'cohort-removed' if after is None else 'cohort-modified',
                    'before': copy.deepcopy(before), 'after': copy.deepcopy(after)})
        if {k: old.get(k) for k in ('title', 'description', 'status')} != {k: new.get(k) for k in ('title', 'description', 'status')}:
            changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'type': 'programme-modified'})
        om, nm = {m['id']: m for m in old.get('modules', [])}, {m['id']: m for m in new.get('modules', [])}
        for mid in sorted(set(nm) - set(om)):
            changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'moduleId': mid, 'moduleName': nm.get(mid, new['modules'][0] if False else new_by[pid]).get('title', mid), 'type': 'module-added'})
        for mid in sorted(set(om) - set(nm)):
            changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'moduleId': mid, 'moduleName': om[mid].get('title', mid), 'type': 'module-removed'})
        for mid in sorted(set(om) & set(nm)):
            a, b = om[mid], nm[mid]
            fields = [k for k in ('title', 'description', 'order', 'sessions', 'credits', 'selection', 'active') if a.get(k) != b.get(k)]
            if fields:
                changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'moduleId': mid, 'moduleName': b.get('title', mid), 'type': 'module-modified', 'fields': fields})
        ok, nk = {k['id']: k for k in old.get('ksbs', [])}, {k['id']: k for k in new.get('ksbs', [])}
        for kid in sorted(set(nk) - set(ok)): changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'ksbId': kid, 'ksbName': nk[kid].get('code', kid), 'type': 'ksb-added'})
        for kid in sorted(set(ok) - set(nk)): changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'ksbId': kid, 'ksbName': ok[kid].get('code', kid), 'type': 'ksb-removed'})
        for kid in sorted(set(ok) & set(nk)):
            if any(ok[kid].get(k) != nk[kid].get(k) for k in ('code', 'kind', 'description', 'status')):
                changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'ksbId': kid, 'ksbName': nk[kid].get('code', kid), 'type': 'ksb-modified'})
        rel = lambda p: {(r.get('ksbId'), r.get('moduleId'), r.get('pathwayId')): r for r in p.get('mappings', []) if r.get('status', 'active') == 'active'}
        orr, nrr = rel(old), rel(new)
        for key in sorted(set(nrr) - set(orr)): changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'type': 'relationship-added', 'ksbId': key[0], 'moduleId': key[1]})
        for key in sorted(set(orr) - set(nrr)): changes.append({'programmeId': pid, 'programmeName': new.get('title', pid), 'type': 'relationship-removed', 'ksbId': key[0], 'moduleId': key[1]})
    if (published or {}).get('intakeCalendar') != (proposed or {}).get('intakeCalendar'):
        changes.append({'type': 'intake-calendar-modified', 'before': copy.deepcopy((published or {}).get('intakeCalendar')),
                        'after': copy.deepcopy((proposed or {}).get('intakeCalendar'))})
    return changes


class PublicationBlocked(ValueError):
    pass


def preflight(layout, *, current_version=None, base_version=None, draft_revision=None,
              expected_revision=None):
    """Validate a draft without writing anything or changing the draft."""
    if not isinstance(layout, dict):
        raise PublicationBlocked("A complete Wizard Layout is required.")
    snapshot = layout.get(KEY)
    validate_structure(snapshot)
    ids = {p["id"] for p in snapshot.get("programmes", [])}
    unsupported = sorted(ids - SUPPORTED)
    if unsupported:
        raise PublicationBlocked("Unsupported programmes: " + ", ".join(unsupported))
    if base_version != current_version:
        raise PublicationBlocked("Published settings changed. Reconcile the shared draft before publishing.")
    if expected_revision is not None and draft_revision != expected_revision:
        raise PublicationBlocked("The shared draft changed. Reload and review the saved changes before publishing.")
    pcp = next((p for p in snapshot["programmes"] if p["id"] == "PCP_L6"), None)
    if pcp is None:
        raise PublicationBlocked("PCP_L6 must remain present for legacy learner compatibility.")
    try:
        resolve_pcp_shadow(snapshot)
    except (ValueError, KeyError) as exc:
        raise PublicationBlocked(str(exc)) from exc
    return {
        "ok": True,
        "version": version(snapshot),
        "programmeStructure": copy.deepcopy(snapshot),
        "programmeModules": project_modules(snapshot),
        "skillsRadarCatalogues": project_catalogues(snapshot),
        "intakeCalendar": copy.deepcopy(snapshot.get("intakeCalendar")) if snapshot.get("intakeCalendar") is not None else None,
        "historicalSnapshotsUnchanged": True,
    }


def build_published_layout(layout, preflight_result):
    """Merge projections into a copy, preserving every unrelated layout field."""
    result = copy.deepcopy(layout)
    result[KEY] = copy.deepcopy(preflight_result["programmeStructure"])
    # Cohort editing must never replace programme-wide legacy projections.
    # Their extra PCP owner/pathway metadata is not representable by generic
    # module/KSB rows. Until an explicit legacy migration exists, reject any
    # legacy-semantic change rather than silently publishing a lossy projection.
    if layout.get('programmeModules') is not None:
        legacy = from_legacy(layout['programmeModules'], layout.get('skillsRadarCatalogues', {}))
        proposed = preflight_result['programmeStructure']
        def legacy_fields(snapshot):
            return [{key: p.get(key) for key in ('id', 'modules', 'ksbs', 'mappings', 'selectionPolicy')}
                    for p in snapshot.get('programmes', [])]
        if legacy_fields(legacy) != legacy_fields(proposed):
            raise PublicationBlocked('Programme-wide legacy data changed. Review compatibility before publication; cohort-only edits are supported.')
    else:
        result['programmeModules'] = copy.deepcopy(preflight_result['programmeModules'])
        result['skillsRadarCatalogues'] = copy.deepcopy(preflight_result['skillsRadarCatalogues'])
    if preflight_result.get("intakeCalendar") is not None:
        result["intakeCalendar"] = copy.deepcopy(preflight_result["intakeCalendar"])
    return result


def sync_published_cohort_statuses(cursor, previous_layout, structure):
    """Apply only newly published status changes on the publisher transaction.

    Draft saves never touch Curriculum eligibility. Existing assignments remain
    untouched; the published structure remains the authority for new learners.
    """
    previous = (previous_layout.get(KEY) or {}).get('programmes', [])
    old = {c.get('id'): c.get('status', 'active')
           for p in previous for c in p.get('cohorts', [])}
    for programme in structure.get('programmes', []):
        curriculum_id = programme.get('curriculumProgrammeId')
        for cohort in programme.get('cohorts', []):
            status = str(cohort.get('status', 'active')).lower()
            status_changed = old.get(cohort.get('id'), '').lower() != status
            if status not in {'planned', 'active', 'completed'}:
                continue
            if not curriculum_id and not str(cohort.get('id', '')).startswith('COHORT-'):
                # Legacy compatibility projections have synthetic Cohort IDs;
                # they have no Curriculum record to synchronize.
                continue
            if not curriculum_id:
                raise PublicationBlocked('A managed Cohort status change needs a verified Curriculum Programme ID.')
            cursor.execute(
                'SELECT programme_id, lower(coalesce(status, \'active\')), cohort_name, programme_name '
                'FROM curriculum.cohorts WHERE cohort_id = %s' +
                (' FOR UPDATE' if cursor.db.vendor == 'postgresql' else ''),
                [cohort['id']],
            )
            real = cursor.fetchone()
            if not real or real[0] != curriculum_id or real[1] in {'archived', 'deleted', 'inactive'}:
                raise PublicationBlocked('A published Cohort status change requires its matching active Curriculum record.')
            name = cohort.get('name')
            if not isinstance(name, str) or not name.strip():
                raise PublicationBlocked('A managed Cohort needs a name before publication.')
            if not status_changed and real[1] != status:
                raise PublicationBlocked('The published Cohort status differs from Curriculum. Review the Cohort before publication.')
            name_changed = real[2] != name
            if name_changed:
                from curriculum_api.cohort_rename import CohortRenameConflict, propagate_cohort_name
                try:
                    propagate_cohort_name({'cohort_id': cohort['id'], 'cohort_name': real[2],
                                           'programme_id': curriculum_id, 'programme_name': real[3]},
                                          name, using=cursor.db.alias)
                except CohortRenameConflict as exc:
                    raise PublicationBlocked(str(exc)) from exc
            if status_changed or name_changed:
                cursor.execute('UPDATE curriculum.cohorts SET cohort_name = %s, status = %s WHERE cohort_id = %s' +
                               (' RETURNING *' if cursor.db.vendor == 'postgresql' else ''),
                               [name, status, cohort['id']])
                if cursor.db.vendor == 'postgresql':
                    from curriculum_api import versioning
                    columns = [column[0] for column in cursor.description]
                    versioning.record_rows('cohorts', [dict(zip(columns, row)) for row in cursor.fetchall()],
                                           using=cursor.db.alias)


def publish(request=None, *, layout=None, base_version=None, revision=None):
    """Publish through the existing append-only Wizard Layout transaction.

    This is a small service adapter, rather than a second persistence path.
    The request is handed to ``programme_structure_publish`` which performs
    authorization, preflight and delegates to ``wizard_layout``'s atomic
    publisher.  The feature remains fail-closed unless explicitly enabled in
    the controlled runtime.
    """
    if not getattr(settings, "PROGRAMME_STRUCTURE_PUBLISHING_ENABLED", False):
        raise PublicationBlocked("Programme Structure publishing is disabled.")
    if request is None:
        raise PublicationBlocked("A request context is required for publication.")
    from enrolment_api.programme_structure_views import programme_structure_publish
    if layout is not None:
        request._body = __import__("json").dumps({
            "structure": layout.get(KEY) if isinstance(layout, dict) else layout,
            "baseVersion": base_version,
            "revision": revision,
        }).encode()
    response = programme_structure_publish(request)
    if getattr(response, "status_code", 500) >= 400:
        raise PublicationBlocked("Programme Structure publication failed.")
    return response
