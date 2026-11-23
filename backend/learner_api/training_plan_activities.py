"""Frozen Activities Overview for NEW official Training Plans; source reads only."""
import copy
import re
from django.db import DatabaseError
from .constants import MAIN_PROVIDER
from .mappers import stored_training_plan
from .module_activities import learner_module_activities
from .programme_meetings import confirmed_programme_meetings, day
from .learning_plan import _totals
from .subject_dates import activity_schedule
from enrollment_service.lms_reviews_readonly import read_document_review_definitions
from enrollment_service.lms_activity_readonly import get_document_week_dates

VERSION = 'activities-overview-v6'


def clean_document_presentation(data):
    """Upgrade an unissued draft's presentation only; retain admin diagnostics.

    No source reread, content replacement, business-data change or persistence.
    Issued/signed revisions must never pass through this helper.
    """
    value = copy.deepcopy(data)
    overview = value.get('activitiesOverview')
    if overview and overview.get('version') in ('activities-overview-v1', 'activities-overview-v2', 'activities-overview-v3', 'activities-overview-v4', 'activities-overview-v5', VERSION):
        # Old custom drafts lack the full Week records (including empty weeks).
        # Retain frozen flat/weekly layouts; only new captures use summary v6.
        if overview.get('version') not in ('activities-overview-v5', VERSION):
            overview['version'] = 'activities-overview-v4'
        for module in overview.get('modules', []):
            for activity in module.get('activities', []):
                if str(activity.get('kind', '')).casefold().replace('-', '_') == 'live_session' and text(activity.get('weekTitle')):
                    activity.setdefault('componentTitle', activity.get('title'))
                    activity['title'] = text(activity['weekTitle'])
                    activity['titleSource'] = 'curriculum.weeks.title'
        nested = value.get('otjh')
        if isinstance(nested, dict) and isinstance(nested.get('activitiesOverview'), dict):
            # Signing envelopes serialize this nested snapshot; keep it aligned
            # with the exact presentation the administrator previewed.
            nested['activitiesOverview'] = copy.deepcopy(overview)
    return value
OPTIONAL = {'module_description_not_recorded', 'ksb_profile_missing_or_ambiguous',
            'ksb_description_unavailable', 'activity_hours_unavailable'}


def text(value):
    return str(value or '').strip()


def assignment_due(component, week, window=None):
    """Only a saved deadline, or the existing end-of-authored-week rule.

    Recording/upload dates and module end dates are never deadlines. An
    undated week cannot be resolved from its ordinal without the verified
    Builder delivery/holiday plan, so it remains explicitly unavailable.
    """
    timing = text((component.get('timing') or {}).get('dueTiming'))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', timing) and day(timing):
        return timing, 'components.settings_json.dueTiming'
    if timing.casefold() == 'end of week' and window:
        return window['end'], window['source']
    schedule = activity_schedule('', section_title=week.get('title', ''))
    if timing.casefold() == 'end of week' and not schedule['date_needs_review'] and schedule.get('week_end'):
        return schedule['week_end'], 'existing authored-week end-of-week deadline rule'
    return None, None


def build(learner):
    """Capture the existing confirmed plan, verified activities and Step 6B dates.

    Never saves a plan, allocates a group, creates a booking or sums activities
    into module totals. Missing identity/content blocks later official issuance.
    """
    from enrolment_api.document_templates.learner_sources import lms_cohort_end
    end, end_issue = lms_cohort_end(learner)
    start = day(learner.start_date)
    result = {'version': VERSION, 'programme': text(learner.programme), 'learnerId': learner.pk,
        'startDate': text(learner.start_date)[:10], 'endDate': end.isoformat() if end else '',
        'endDateSource': 'LMS curriculum.cohorts.end_date',
        'deliveryResponsibility': MAIN_PROVIDER,
        'deliverySchedule': 'Activities follow the recorded LMS order within each module. Dates are shown only for verified assignment deadlines and planned Monthly Coaching Meetings. Projected dates are not appointment bookings.',
        'modules': [], 'reviews': [], 'unassignedMeetings': [], 'unclassifiedMeetings': [], 'blockers': [], 'warnings': []}
    if not start:
        result['blockers'].append({'code': 'saved_learner_dates_required', 'message': 'The learner needs a valid saved programme start date.'})
    if end_issue:
        result['blockers'].append({'code': end_issue, 'database': 'LMS', 'schema': 'curriculum', 'table': 'cohorts',
            'columns': ['end_date'], 'message': 'The assigned home-cohort programme end date could not be verified. No local date was substituted.'})
    if start and end and start > end:
        result['blockers'].append({'code': 'programme_period_conflict', 'message': 'The saved learner start date is after the assigned LMS cohort end date.'})
    plan = stored_training_plan(learner)
    if not plan:
        result['blockers'].append({'code': 'persisted_learning_plan_required', 'message': 'Save the confirmed learner Learning Plan before creating this document.'})
    try:
        activities = learner_module_activities(learner.pk)
        meeting_result = confirmed_programme_meetings(learner)
    except DatabaseError:
        activities = {'modules': [], 'blockers': [{'code': 'source_unavailable', 'message': 'Confirmed learner activities could not be read.'}]}
        meeting_result = None
    result['sourceDiagnostics'] = copy.deepcopy(activities.get('blockers', []))
    optional_messages = {
        'module_description_not_recorded': 'A module description is not recorded and is shown as unavailable.',
        'ksb_description_unavailable': 'Some mapped KSB descriptions are not recorded; only verified covered codes are displayed.',
        'activity_hours_unavailable': 'Some activities have no recorded independent hours and are displayed with a dash.',
    }
    for issue in activities.get('blockers', []):
        if issue.get('code') == 'ksb_profile_missing_or_ambiguous':
            # Profile enrichment is not required for already verified module
            # mappings. Retain diagnostics without printing irrelevant DB detail.
            continue
        value = copy.deepcopy(issue)
        if issue.get('code') in OPTIONAL:
            value['message'] = optional_messages[issue['code']]
        result['warnings' if issue.get('code') in OPTIONAL else 'blockers'].append(value)
    if meeting_result is None:
        meetings = []
        result['blockers'].append({'code': 'confirmed_programme_reviews_unavailable', 'message': 'The confirmed programme review context is unavailable.'})
    else:
        meetings, reason = meeting_result
        result['blockers'].extend(copy.deepcopy(getattr(meeting_result, 'blockers', [])))
        if reason in ('no-dates', 'unavailable') and not getattr(meeting_result, 'blockers', []):
            result['blockers'].append({'code': 'programme_reviews_' + reason, 'message': 'Programme review dates could not be verified.'})
    week_windows = {}
    for module in activities.get('modules', []):
        rows = []
        windows = []
        week_records = []
        calendar = get_document_week_dates(module['lmsModuleId'], module['programmeId'], module['cohortId'],
            module['groupId'], module.get('weeks') or []) if module.get('weeks') else {'weeks': {}, 'blockers': []}
        result['sourceDiagnostics'].extend(calendar['blockers'])
        result['warnings'].extend(calendar['blockers'])
        for week in module.get('weeks') or []:
            week_records.append({k: copy.deepcopy(week.get(k)) for k in
                ('sourceId', 'weekNumber', 'order', 'title', 'summary', 'learningOutcomes')})
            schedule = activity_schedule('', section_title=week.get('title', ''))
            window = calendar['weeks'].get(week['sourceId'])
            week_records[-1]['componentsAvailable'] = week.get('components') is not None
            if window:
                week_records[-1].update(startDate=window['start'], endDate=window['end'])
                windows.append({'id': week['sourceId'], **window})
            elif not schedule['date_needs_review'] and schedule.get('week_start'):
                windows.append({'id': week['sourceId'], 'start': schedule['week_start'], 'end': schedule['week_end']})
                week_records[-1].update(startDate=schedule['week_start'], endDate=schedule['week_end'])
            for component in week.get('components') or []:
                due, due_source = assignment_due(component, week, window) if component['type'].casefold() == 'assignment' else (None, None)
                if component['type'].casefold() == 'assignment' and not due:
                    result['warnings'].append({'code': 'assignment_due_date_unresolved', 'sourceId': component['sourceId'],
                        'database': 'LMS', 'schema': 'curriculum', 'table': 'components', 'columns': ['settings_json'],
                        'message': f"{component['title']}: no resolvable saved assignment due date. dueTiming requires a verified dated week; module end and recording dates have not been substituted."})
                rows.append({'id': component['sourceId'], 'kind': component['type'],
                    'title': text(week['title']) if component['type'].casefold().replace('-', '_') == 'live_session' and text(week['title']) else component['title'],
                    'componentTitle': component['title'],
                    'titleSource': 'curriculum.weeks.title' if component['type'].casefold().replace('-', '_') == 'live_session' and text(week['title']) else 'curriculum.components.title',
                    'weekId': week['sourceId'], 'weekTitle': week['title'],
                    'hours': component.get('activityOtjHours'),
                    'date': due, 'dateSource': due_source, 'timing': '', 'source': 'LMS component'})
                for topic in component.get('topics') or []:
                    # A topic has no independent hours. Never repeat assignment hours.
                    rows.append({'id': component['sourceId'] + ':topic:' + topic['id'], 'kind': 'topic',
                        'title': text(topic.get('name')) or text(topic.get('question')) or text(topic.get('instructions')),
                        'hours': None, 'date': None, 'timing': '', 'source': 'LMS assignment topic',
                        'weekId': week['sourceId'], 'weekTitle': week['title'],
                        'parentComponentId': component['sourceId']})
        week_windows[module['academicModuleId']] = windows
        result['modules'].append({'academicModuleId': module['academicModuleId'],
            'lmsOfferingId': module['lmsModuleId'], 'title': module.get('lmsTitle') or module['title'],
            'description': module.get('description'), 'coveredKsbs': copy.deepcopy(module.get('coveredKsbs') or []),
            'hours': module.get('moduleTotalOtjHours'), 'hoursPending': module.get('moduleHoursPending', False),
            'startDate': module.get('startDate'), 'endDate': module.get('endDate'), 'weeks': week_records, 'activities': rows})
        module_start, module_end = day(module.get('startDate')), day(module.get('endDate'))
        if not module_start or not module_end or module_start > module_end:
            result['blockers'].append({'code': 'module_schedule_unavailable',
                'message': 'A confirmed module has no valid verified delivery period.', 'academicModuleId': module['academicModuleId']})
        elif start and end and (module_start < start or module_end > end):
            result['blockers'].append({'code': 'module_outside_saved_programme_period', 'database': 'LMS',
                'schema': 'curriculum', 'table': 'cohorts', 'columns': ['end_date'],
                'academicModuleId': module['academicModuleId'],
                'message': f"{module.get('lmsTitle') or module['title']}: its recorded module period ({module_start} to {module_end}) falls outside the saved learner start / assigned LMS home-cohort end ({start} to {end}). Review the source dates before issuance."})
    if plan and len(result['modules']) != len(plan):
        result['blockers'].append({'code': 'confirmed_module_bundle_incomplete', 'message': 'Not every confirmed Learning Plan module could be verified.'})
    if any(m['hoursPending'] or m['hours'] is None for m in result['modules']):
        result['blockers'].append({'code': 'module_total_hours_unavailable', 'message': 'A confirmed module has no verified module OTJ total.'})
    # Existing plan totals are authoritative; no component or topic aggregation.
    module_totals = [{'hours': m['hours']} for m in result['modules']]
    result['totals'] = _totals(module_totals, meetings)
    programme_ids = {m['programmeId'] for m in activities.get('modules', []) if m.get('programmeId')}
    definitions = read_document_review_definitions(next(iter(programme_ids)), [m['reviewTemplateId'] for m in meetings]) if len(programme_ids) == 1 else {'definitions': {}, 'blockers': []}
    result['sourceDiagnostics'].extend(definitions['blockers'])
    result['warnings'].extend(copy.deepcopy(definitions['blockers']))
    from curriculum_api.review_instances import required_signature_roles
    from curriculum_api.review_pdf_presentation import ROLE_LABELS
    seen = set()
    for meeting in meetings:
        definition = definitions['definitions'].get(meeting['reviewTemplateId'], {})
        kind = definition.get('typeCode')
        if kind is None:
            result['blockers'].append({'code': 'review_type_unavailable', 'database': 'LMS', 'schema': 'curriculum',
                'table': 'review_templates', 'columns': ['review_type_id'],
                'message': f"{meeting['name']}: the authoritative Review Type cannot be read; coaching/progress placement cannot be verified."})
        roles = required_signature_roles(definition['signatures']) if definition.get('signatures') is not None else None
        participants = ', '.join(ROLE_LABELS.get(role, role) for role in roles) if roles else 'No required signatories configured' if roles == () else None
        if kind == 'progress_review' and roles is None:
            result['warnings'].append({'code': 'review_signatories_unavailable', 'reviewTemplateId': meeting['reviewTemplateId'],
                'message': f"{meeting['name']}: required signatory roles could not be read from curriculum.review_templates.signature_*; no names or completed signatures have been inferred."})
        for value in meeting.get('plannedDates', []):
            identity = (meeting['reviewTemplateId'], value)
            if identity in seen:
                continue
            seen.add(identity)
            occurrence = {'id': meeting['reviewTemplateId'] + ':' + value, 'templateId': meeting['reviewTemplateId'],
                'title': meeting['name'], 'date': value, 'hours': meeting['hoursEach'],
                'scheduleKind': 'projected', 'participants': participants, 'requiredRoles': list(roles) if roles is not None else None,
                'reviewTypeCode': kind, 'executionEligibleNow': meeting.get('executionEligibleNow')}
            if kind == 'progress_review':
                result['reviews'].append(occurrence)
            elif kind is None:
                # Preserve unresolved occurrences separately; never classify by
                # editable name or silently omit potential coaching/progress.
                result['unclassifiedMeetings'].append(occurrence)
            if kind != 'mcm':
                continue
            when = day(value)
            targets = [m for m in result['modules'] if when and day(m['startDate']) and day(m['endDate'])
                and day(m['startDate']) <= when <= day(m['endDate'])]
            if len(targets) == 1:
                rows = targets[0]['activities']
                weeks = week_windows[targets[0]['academicModuleId']]
                matches = [w for w in weeks if day(w['start']) <= when <= day(w['end'])]
                if len(matches) == 1:
                    wid = matches[0]['id']
                    indices = [i for i, r in enumerate(rows) if r.get('weekId') == wid]
                    if indices:
                        position = next((i for i in indices if rows[i].get('date') and day(rows[i]['date']) > when), max(indices) + 1)
                    else:
                        order = {w['id']: i for i, w in enumerate(weeks)}
                        position = next((i for i, r in enumerate(rows) if order.get(r.get('weekId'), -1) > order[wid]), len(rows))
                    rows.insert(position, {**occurrence, 'kind': 'meeting', 'weekId': wid,
                        'timing': '', 'source': 'Programme review projection'})
                    continue
            result['unassignedMeetings'].append(occurrence)
    result['reviews'].sort(key=lambda r: (r['date'], r['id']))
    if result['unassignedMeetings']:
        result['warnings'].append({'code': 'meeting_module_range_unverified',
            'message': 'Monthly Coaching dates could not be placed in exactly one verified module/week: ' +
                ', '.join(r['date'] for r in result['unassignedMeetings']) + '. No random week has been assigned.'})
    from .compact_training_plan import summarise
    for module in result['modules']:
        module['weekSummaries'], diagnostics = summarise(module)
        by_week = {w['id']: w for w in module['weekSummaries']}
        for row in module['activities']:
            if row.get('reviewTypeCode') != 'mcm':
                continue
            week = by_week.get(row.get('weekId'), {})
            if week.get('kind') != 'assignment' or (week.get('dueDates') and row['date'] < week['dueDates'][0]):
                diagnostics.append({'code': 'coaching_assignment_placement_unverified',
                    'sourceId': row['id'], 'weekId': row.get('weekId'), 'lmsOfferingId': module['lmsOfferingId'],
                    'message': 'The verified coaching Week/date does not support placement after an Assignment. The actual Week/date is retained.'})
        result['sourceDiagnostics'].extend(copy.deepcopy(diagnostics))
        result['warnings'].extend(diagnostics)
    result['finalisationAllowed'] = not result['blockers']
    return result


def require_ready(overview):
    from enrolment_api.document_templates.registry import TemplateError
    if overview and overview.get('version') in ('activities-overview-v1', 'activities-overview-v2', 'activities-overview-v3', 'activities-overview-v4', 'activities-overview-v5', VERSION) and not overview.get('finalisationAllowed'):
        reasons = '; '.join(i.get('message') or i.get('code', 'Source unavailable') for i in overview.get('blockers', []))
        raise TemplateError('Training Plan cannot be issued: ' + reasons)


def new_content(content):
    """Upgrade only newly captured content, retaining every mandatory section."""
    value = copy.deepcopy(content)
    found = False
    for index, block in enumerate(value['blocks']):
        if block.get('source') == 'learning-plan':
            found = True
            if index and value['blocks'][index - 1].get('type') == 'heading':
                value['blocks'][index - 1]['text'] = 'Training Plan - Activities Overview'
                value['blocks'][index - 1].pop('runs', None)
    if not found:
        index = next((i for i, b in enumerate(value['blocks']) if b.get('type') == 'signature'), len(value['blocks']))
        value['blocks'][index:index] = [{'id': 'activities-overview-heading', 'type': 'heading', 'level': 2,
            'text': 'Training Plan - Activities Overview'}, {'id': 'activities-overview', 'type': 'native',
            'source': 'learning-plan', 'label': 'Activities Overview'}]
    return value
