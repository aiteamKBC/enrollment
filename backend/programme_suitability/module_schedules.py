"""GET-only schedule projection. Review candidates never constitute approvals."""
import json
import re
import html
import unicodedata
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from django.http import JsonResponse
from django.utils import timezone
from enrolment_api.auth import enrolment_login_required
from enrollment_service import lms_readonly
from .models import Assessment
from .permissions import is_staff
from .modules_rules import available_modules
from .home_cohort import read_home_cohort
from .views import learner_record

PROGRAMMES = {'ME_L4': 'PROG-ME-L4', 'MM_L6': 'PROG-MM-L6',
              'APM_L4': 'PROG-20260907141619067932', 'PCP_L6': 'PROG-PCP-L6'}


def read_all(programme_id, cohort_id=None):
    rows, seen = [], set()
    for offset in range(0, 100001, 200):
        page = lms_readonly.get_module_offerings(programme_id, cohort_id=cohort_id, limit=200, offset=offset)
        for row in page:
            if row['module_catalogue_id'] in seen:
                raise lms_readonly.LmsReadUnavailable('schedule_changed_retry')
            seen.add(row['module_catalogue_id'])
            rows.append(row)
        if len(page) < 200:
            return rows
    raise lms_readonly.LmsReadUnavailable('schedule_result_incomplete')


def eligible(row, programme_id, today):
    # The reader already enforces composite joins, date validity and all deletion
    # flags. Recheck response identities/dates and exclude test-labelled deliveries.
    text = ' '.join(str(row.get(k) or '') for k in ('title', 'cohort_name', 'group_name', 'cohort_source_type'))
    if re.search(r'\b(test|testing|dummy|demo|sandbox|copy|verification|ay\s+haga)\b', text, re.I):
        return False
    try:
        start, end = date.fromisoformat(str(row['start_date'])), date.fromisoformat(str(row['end_date']))
    except (KeyError, ValueError, TypeError):
        return False
    return (row.get('programme_id') == programme_id and bool(row.get('cohort_id'))
            and str(row.get('cohort_status', '')).lower() in {'planned', 'active'}
            and start > today and end >= start)


def normalized_title(title):
    # Preserve qualification words (APMG/APM/PMP), word order and all content.
    # Only Unicode, HTML entities, case, punctuation and '&' versus 'and' differ.
    text = unicodedata.normalize('NFKC', html.unescape(str(title))).casefold().replace('&', ' and ')
    return ' '.join(re.findall(r'[^\W_]+', text, re.UNICODE))


def matching_confidence(module, title, possible_titles):
    if normalized_title(module['title']) == normalized_title(title):
        return 'exact_title', 'Exact or safely normalized title in the confirmed programme; not a confirmed booking.'
    if any(normalized_title(alias) == normalized_title(title) for alias in possible_titles):
        return 'tentative', 'Documented possible title; academic/qualification equivalence is uncertain.'
    return None, None


def project(programme, pathway, modules, home, *, review=False, today=None):
    today = today or timezone.now().astimezone(ZoneInfo('Europe/London')).date()
    evidence = json.loads(Path(__file__).with_name('schedule_review_candidates.json').read_text(encoding='utf-8'))
    # These documented candidates never imply verified qualification equivalence.
    academic = {m['id']: m['title'] for m in modules}
    titles = {(m['programme'], m['pathway'], m['academic_id']): tuple(m['titles']) for m in evidence['modules']
              if academic.get(m['academic_id']) == m['academic_title']}
    keys = [(programme, pathway, m['id']) for m in modules]
    home_id = home.get('cohortId') if home.get('status') == 'resolved' else None
    fixed_pending = programme != 'PCP_L6' and not home_id
    candidates = []
    if modules and not fixed_pending:
        candidates = [r for r in read_all(PROGRAMMES[programme], None if programme == 'PCP_L6' else home_id)
                      if eligible(r, PROGRAMMES[programme], date.min)
                      and (programme == 'PCP_L6' or r['cohort_id'] == home_id)]
    results = []
    for module, key in zip(modules, keys):
        possible_titles = titles.get(key, ())
        matches = []
        historical_match = False
        for row in candidates:
            confidence, confidence_reason = matching_confidence(module, row['title'], possible_titles)
            if not confidence:
                continue
            if not eligible(row, PROGRAMMES[programme], today):
                historical_match = True
                continue
            matches.append({'id': row['module_catalogue_id'], 'moduleId': module['id'],
                            'academicTitle': module['title'], 'lmsTitle': row['title'],
                            'programmeId': row['programme_id'], 'delivery': row['cohort_id'],
                            'cohortId': row['cohort_id'], 'cohortName': row['cohort_name'],
                            'groupId': row.get('group_id'), 'group': row.get('group_name') or '',
                            'start': str(row['start_date']), 'end': str(row['end_date']),
                            'approved': False, 'reviewOnly': review,
                            'matchConfidence': confidence, 'confidenceReason': confidence_reason,
                            'provisional': confidence == 'tentative', 'allowLocalSelection': not review and programme == 'PCP_L6'})
        matches.sort(key=lambda r: (r['start'], r['end'], r['id']))
        if matches:
            reason = 'Possible LMS match; any choice is provisional and is not a confirmed booking.' if any(o['provisional'] for o in matches) else 'Exact LMS title match; session choices remain local and unsaved.'
        elif fixed_pending:
            reason = 'Your saved home cohort is pending; other cohorts cannot be substituted.'
        elif historical_match:
            reason = 'Matching LMS deliveries have already started; no eligible future session is available.'
        elif possible_titles:
            reason = 'No eligible delivery was found for the documented possible LMS titles.'
        else:
            reason = 'No reliable LMS counterpart or future delivery was found for this academic module.'
        results.append({'academicModuleId': module['id'], 'academicTitle': module['title'], 'reason': reason, 'offerings': matches})
    return {'programmeId': programme, 'pathway': pathway, 'review': review, 'asOfDate': today.isoformat(),
            'homeCohort': home, 'modules': results, 'offeringCount': sum(len(m['offerings']) for m in results)}


@enrolment_login_required
def learner_module_schedule(request, kind, learner_id):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    review = request.GET.get('review') == '1'
    # Enforce staff rights even when the legacy development auth bypass is on.
    if review and not is_staff(request):
        return JsonResponse({'error': 'Staff sign-in is required to review unverified LMS matches.'}, status=403)
    response = None
    try:
        if learner_record(kind, learner_id) is None:
            return JsonResponse({'error': 'Not found.'}, status=404)
        row = Assessment.objects.filter(learner_id=learner_id, learner_kind=kind).first()
        if not row or not row.selection_confirmed_at or not row.modules_configuration:
            return JsonResponse({'error': 'A confirmed programme and saved module configuration are required.'}, status=409)
        # Do NOT use pinned_configuration(): it can refresh/pin and write records.
        programme = next((p for p in row.modules_configuration['programmes'] if p['id'] == row.selected_programme), None)
        if not programme or programme['id'] not in PROGRAMMES:
            return JsonResponse({'error': 'The saved programme is unavailable.'}, status=409)
        pathway = None
        if programme['id'] == 'PCP_L6':
            saved = row.modules_selection or {}
            pathway = request.GET.get('pathway') or saved.get('pathway')
            path = next((p for p in programme['pathways'] if p['id'] == pathway and p.get('active')), None)
            if not path:
                return JsonResponse({'error': 'Choose an available active pathway first.'}, status=400)
            available = available_modules(path['modules']) if path else []
            # A read projection of current UI choices; never saves or validates
            # credits on their behalf. Academic validation remains unchanged.
            raw = request.GET.get('modules')
            if raw is not None and len(raw) > 4096:
                return JsonResponse({'error': 'Too many requested module identifiers.'}, status=400)
            ids = raw.split(',') if raw is not None and len(raw) <= 4096 else (saved.get('modulesByPathway') or {}).get(pathway, [])
            allowed = {m['id'] for m in available}
            if not isinstance(ids, list) or any(mid not in allowed for mid in ids):
                return JsonResponse({'error': 'Unknown module in the requested active pathway.'}, status=400)
            chosen = set(ids) | {m['id'] for m in available if m.get('isMandatory')}
            modules = [m for m in available if m['id'] in chosen]
        else:
            modules = [m for m in programme['modules'] if m.get('active', True)]
        home = read_home_cohort(row, kind, learner_id, row.modules_configuration_version)
        response = JsonResponse(project(programme['id'], pathway, modules, home, review=review))
    except lms_readonly.LmsReadUnavailable:
        response = JsonResponse({'error': 'LMS schedules are temporarily unavailable. Retry the read.'}, status=503)
    except Exception:
        # Never expose DSNs, driver messages, raw settings or learner data.
        response = JsonResponse({'error': 'Could not read the saved module schedules.'}, status=503)
    response['Cache-Control'] = 'private, no-store'
    return response
