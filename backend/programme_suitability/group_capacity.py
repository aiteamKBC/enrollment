"""Enrolment-owned capacity ledger shared by preview and PCP confirmation.

Only an explicit PCP confirmation request may allocate through the learner API.
No learner/account/profile, Learning Plan, document, cohort or LMS writes occur.
"""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

from django.db import DatabaseError, transaction
from learner_api.models import EnrolmentUser, LearnerProfile
from enrollment_service import lms_readonly
from .models import Assessment, CohortGroupAllocation, CohortGroupPool
from .programme_identity import CURRICULUM_PROGRAMME_IDS
from .lms_placement import group_key, label_key, curriculum_programme_id
from .module_items import is_group
from .modules_rules import available_modules
from .modules_service import selection_complete


class CapacityUnavailable(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def unavailable(code, message):
    raise CapacityUnavailable(code, message)


@dataclass(frozen=True)
class CapacityContext:
    learner_id: int
    learner_kind: str
    programme: str
    programme_id: str
    cohort_id: str
    group_id: str
    pathway: str
    modules: tuple
    configuration_version: int
    revision: int


def context_for(row, using='default', *, lock=False):
    if not row.selection_confirmed_at or row.selected_programme not in CURRICULUM_PROGRAMME_IDS:
        unavailable('programme_unconfirmed', 'A saved confirmed programme is required.')
    programme = next((p for p in (row.modules_configuration or {}).get('programmes', []) if p['id'] == row.selected_programme), None)
    if not programme or row.modules_configuration_version is None:
        unavailable('academic_modules_unavailable', 'The saved academic module configuration needs review.')
    pathway = ''
    if row.selected_programme == 'PCP_L6':
        if not selection_complete(row, row.modules_selection):
            unavailable('academic_modules_incomplete', 'Save a complete active pathway and its academic module choices first.')
        choice = row.modules_selection
        pathway = choice['pathway']
        path = next(p for p in programme['pathways'] if p['id'] == pathway and p.get('active'))
        ids = choice['modulesByPathway'][pathway]
        modules = [m for m in available_modules(path['modules']) if m['id'] in ids]
    else:
        def flatten(items):
            return [m for item in items if item.get('active', True)
                    for m in (flatten(item.get('items', [])) if is_group(item) else [item])]
        modules = flatten(programme.get('modules', []))
    if not modules or len({m['id'] for m in modules}) != len(modules):
        unavailable('academic_modules_unavailable', 'The saved academic module bundle is empty or ambiguous.')
    sources = EnrolmentUser.all_learners.using(using).only('programme', 'cohort', 'group')
    profiles_query = LearnerProfile.objects.using(using).filter(enrolment_id=row.learner_id)
    if lock:
        sources = sources.select_for_update()
        profiles_query = profiles_query.select_for_update()
    source = sources.get(pk=row.learner_id)
    pid = CURRICULUM_PROGRAMME_IDS[row.selected_programme]
    if curriculum_programme_id('', source.programme) != pid:
        unavailable('programme_identity_unresolved', 'The saved programme identity needs admissions review.')
    profiles = list(profiles_query.only('programme_id', 'cohort_id', 'cohort', 'group_id', 'group_name')[:2])
    if len(profiles) > 1:
        unavailable('placement_ambiguous', 'The saved learner profile needs admissions review.')
    profile = profiles[0] if profiles else None
    assignment = (row.review or {}).get('programmeAssignment') or {}
    if profile and profile.programme_id and profile.programme_id != pid:
        unavailable('programme_identity_unresolved', 'The saved programme identities disagree.')
    cid = profile.cohort_id if profile and profile.cohort_id else assignment.get('cohortId')
    if profile and profile.cohort and label_key(profile.cohort) != label_key(source.cohort):
        unavailable('cohort_identity_unresolved', 'The saved home-cohort identities disagree.')
    cohorts = lms_readonly.get_programme_cohorts(pid)
    matches = [c for c in cohorts if c['programme_id'] == pid and
               (c['cohort_id'] == cid if cid else label_key(c['cohort_name']) == label_key(source.cohort))]
    if not source.cohort or len(matches) != 1 or label_key(matches[0]['cohort_name']) != label_key(source.cohort):
        unavailable('cohort_identity_unresolved', 'Admissions must verify the saved home-cohort identity; it has not been changed.')
    cid = matches[0]['cohort_id']
    gid = profile.group_id if profile and profile.group_id else ''
    if source.group:
        groups = lms_readonly.get_cohort_groups(pid, cid)
        selected = [g for g in groups if g['programme_id'] == pid and g['cohort_id'] == cid
                    and group_key(g['group_name']) == group_key(source.group)]
        if len(selected) != 1 or (gid and gid != selected[0]['group_id']):
            unavailable('group_identity_unresolved', 'The saved group identity needs admissions review.')
        gid = selected[0]['group_id']
    return CapacityContext(row.learner_id, row.learner_kind, row.selected_programme, pid, cid, gid,
                           pathway, tuple(modules), row.modules_configuration_version, row.modules_revision)


def verified_roster(pool, groups):
    roster = pool.verified_members if pool else None
    if not pool or not pool.verified_at or not pool.verification_evidence.strip() or not isinstance(roster, dict):
        unavailable('memberships_unverified', 'Admissions must verify existing home-cohort group memberships before a group can be prepared.')
    if set(roster) != {g['group_id'] for g in groups}:
        unavailable('memberships_unverified', 'The verified membership baseline does not cover the current home-cohort groups.')
    seen = set()
    for members in roster.values():
        if (not isinstance(members, list) or any(type(i) is not int or i <= 0 for i in members)
                or len(set(members)) != len(members) or seen.intersection(members)):
            unavailable('memberships_unverified', 'Existing membership identities are ambiguous or duplicated.')
        seen.update(members)
    return roster


def complete_bundle(context, group, candidates):
    from .module_schedules import eligible, matching_confidence
    evidence = json.loads(Path(__file__).with_name('schedule_review_candidates.json').read_text(encoding='utf-8'))
    result, used = [], set()
    candidates = [r for r in candidates if r.get('cohort_id') == context.cohort_id and r.get('group_id') == group['group_id']
                  and eligible(r, context.programme_id, date.min)]
    for module in context.modules:
        aliases = next((e.get('confirmed_titles', []) for e in evidence['modules'] if
            e['programme'] == context.programme and e['pathway'] == (context.pathway or None)
            and e['academic_id'] == module['id'] and e['academic_title'] == module['title']), [])
        matches = [r for r in candidates if matching_confidence(module, r['title'], (), aliases)[0]]
        if len(matches) != 1 or matches[0]['module_catalogue_id'] in used:
            unavailable('schedule_incomplete', 'A complete, unique home-group module schedule must be verified before allocation.')
        r = matches[0]
        used.add(r['module_catalogue_id'])
        result.append({'academicModuleId': module['id'], 'offeringId': r['module_catalogue_id'],
            'cohortId': context.cohort_id, 'groupId': group['group_id'], 'title': r['title'],
            'start': str(r['start_date']), 'end': str(r['end_date'])})
    return result


def result_for(allocation):
    return {'status': 'allocated', 'groupId': allocation.group_id, 'groupName': allocation.group_name,
            'cohortId': allocation.pool.cohort_id, 'pathway': allocation.pathway,
            'academicModuleIds': allocation.academic_ids, 'offerings': allocation.offerings,
            'configurationVersion': allocation.configuration_version,
            'selectionRevision': allocation.selection_revision, 'confirmedAt': allocation.created_at.isoformat()}


def choose(context, using='default', lock=False):
    existing = CohortGroupAllocation.objects.using(using).filter(learner_id=context.learner_id).select_related('pool').first()
    if existing:
        if (existing.learner_kind != context.learner_kind or existing.pool.programme_id != context.programme_id
                or existing.pool.cohort_id != context.cohort_id or existing.pathway != context.pathway
                or set(existing.academic_ids) != {m['id'] for m in context.modules}
                or (context.group_id and context.group_id != existing.group_id)):
            unavailable('allocation_locked', 'An existing permanent allocation requires admissions review; it will not be replaced.')
        return existing, None
    pools = CohortGroupPool.objects.using(using)
    if lock:
        pools = pools.select_for_update()
    pool = pools.filter(programme_id=context.programme_id, cohort_id=context.cohort_id).first()
    groups = lms_readonly.get_cohort_groups(context.programme_id, context.cohort_id)
    if any(g['programme_id'] != context.programme_id or g['cohort_id'] != context.cohort_id for g in groups):
        unavailable('group_identity_unresolved', 'LMS groups do not match the saved home cohort.')
    roster = verified_roster(pool, groups)
    allocations = list(CohortGroupAllocation.objects.using(using).filter(pool_id=pool.pk).values('learner_id', 'group_id', 'seat'))
    owners = [gid for gid, members in roster.items() if context.learner_id in members]
    if context.group_id and owners != [context.group_id]:
        unavailable('memberships_unverified', 'The existing saved group must be reconciled with the verified membership baseline.')
    from .module_schedules import read_all
    candidates = read_all(context.programme_id, context.cohort_id)
    for number in (1, 2):
        matches = [g for g in groups if group_key(g['group_name']) == f'g{number}']
        if len(matches) > 1:
            unavailable('group_identity_unresolved', 'Duplicate numbered groups require admissions review.')
        if not matches:
            # No Group 1 means the normal first-group sequence is unavailable.
            if number == 1 and not owners:
                unavailable('group_unavailable', 'A real home-cohort Group 1 is required.')
            continue
        group = matches[0]
        gid = group['group_id']
        if owners and owners != [gid]:
            continue
        baseline = roster[gid]
        current = [a for a in allocations if a['group_id'] == gid]
        members = set(baseline) | {a['learner_id'] for a in current}
        if context.learner_id not in members and len(members) >= 20:
            continue
        schedule = complete_bundle(context, group, candidates)
        occupied = set(range(1, len(baseline) + 1)) | {a['seat'] for a in current}
        seat = baseline.index(context.learner_id) + 1 if context.learner_id in baseline else next((i for i in range(1, 21) if i not in occupied), None)
        if seat is None or seat > 20:
            unavailable('capacity_unavailable', 'The home-cohort group is full and requires admissions review.')
        return None, (pool, group, seat, schedule)
    unavailable('capacity_unavailable', 'No home-cohort group has a verified place. Group 2 must exist with a complete schedule before further allocations.')


def allocate(kind, learner_id, expected_revision, *, using='default', pcp_only=False):
    """Explicit confirmation integration point, using the existing capacity ledger.

    Writes only the Enrolment-owned ledger on ONE connection. Group, seat and
    complete offering snapshot are persisted together in this transaction.
    """
    with transaction.atomic(using=using):
        row = Assessment.objects.using(using).select_for_update().get(learner_id=learner_id, learner_kind=kind)
        if pcp_only and row.selected_programme != 'PCP_L6':
            unavailable('pcp_only', 'Cohort module confirmation is available only for PCP Level 6.')
        context = context_for(row, using, lock=True)
        existing, candidate = choose(context, using, lock=True)
        if existing:
            return result_for(existing)
        if type(expected_revision) is not int or expected_revision != context.revision:
            unavailable('selection_changed', 'The saved academic choices changed. Reload before confirming.')
        pool, group, seat, schedule = candidate
        allocation = CohortGroupAllocation.objects.using(using).create(learner_id=learner_id, learner_kind=kind,
            pool_id=pool.pk, group_id=group['group_id'], group_name=group['group_name'], seat=seat,
            pathway=context.pathway, academic_ids=[m['id'] for m in context.modules], offerings=schedule,
            configuration_version=context.configuration_version, selection_revision=context.revision)
        return result_for(allocation)


def preview_home_group(row, home):
    """GET-only. Preserve an explicit saved group even before ledger rollout.

    No new assignment is inferred from a first offering or a group label alone.
    Missing capacity tables must never hide an existing learner's saved group.
    """
    if home.get('status') == 'resolved' and home.get('source') == 'lms' and home.get('groupId'):
        return {'status': 'assigned', 'groupId': home['groupId'], 'groupName': home.get('groupName'),
                'message': 'Showing your saved home-cohort group. No new allocation has been made.'}
    try:
        # Savepoint confines a missing migration error to this optional read.
        with transaction.atomic(using='default'):
            context = context_for(row)
            if home.get('status') != 'resolved' or context.cohort_id != home.get('cohortId'):
                unavailable('cohort_identity_unresolved', 'Admissions must verify the saved home-cohort identity.')
            existing, candidate = choose(context)
            if existing:
                return {**result_for(existing), 'message': 'Showing the existing permanent home-group allocation.'}
            group = candidate[1]
            return {'status': 'eligible', 'groupId': group['group_id'], 'groupName': group['group_name'],
                    'message': 'Eligible home-cohort group shown for information only. No seat has been reserved.'}
    except CapacityUnavailable as error:
        code, message = error.code, str(error)
    except DatabaseError:
        code, message = 'capacity_preparation_pending', 'Home-group allocation is not available yet. Admissions must verify memberships and complete home-cohort schedules.'
    except (lms_readonly.LmsReadUnavailable, EnrolmentUser.DoesNotExist):
        code, message = 'source_unavailable', 'The saved home-cohort group and schedules could not be verified. Admissions review is required.'
    return {'status': 'unavailable', 'groupId': None, 'groupName': None, 'code': code, 'message': message}
