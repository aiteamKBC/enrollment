"""Project staff attendance into completion without writing progress or hours."""
from collections import Counter
from datetime import date, timezone as utc
from math import isfinite
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.utils import timezone


UK = ZoneInfo('Europe/London')
INVALID = {'cancelled', 'canceled', 'deleted', 'failed', 'superseded'}


def _text(value):
    return ' '.join(str(value or '').split()).casefold()


def _settings(component):
    value = component.get('settings_json') or {}
    return value if isinstance(value, dict) else {}


def _aware(value):
    return timezone.make_aware(value, utc.utc) if value and timezone.is_naive(value) else value


def _matched_occurrences(components, occurrences):
    matched = {}
    for component in components:
        settings = _settings(component)
        legacy = settings.get('legacySettings') or {}
        legacy = legacy if isinstance(legacy, dict) else {}
        occurrence_id = settings.get('teamsOccurrenceId') or legacy.get('teamsOccurrenceId')
        series_id = settings.get('teamsLiveSessionId') or legacy.get('teamsLiveSessionId')
        number = settings.get('teamsSessionNumber') or legacy.get('teamsSessionNumber')
        day = str(settings.get('sessionDate') or legacy.get('sessionDate') or '')[:10]
        matches = []
        for row in occurrences:
            if row['module_catalogue_id'] != component['module_catalogue_id']:
                continue
            if occurrence_id:
                same = str(occurrence_id) in {str(row['session_id']), str(row.get('attendance_source_occurrence_id') or '')}
            elif series_id and number:
                same = str(series_id) == str(row.get('live_session_id')) and str(number) == str(row.get('session_number'))
            else:
                same = bool(day) and day == str(row['session_date']) and (
                    not series_id or str(series_id) == str(row.get('live_session_id')))
            if same:
                matches.append(row)
        if len(matches) == 1:
            matched[component['id']] = matches[0]
    return matched


def project_manual_completion(components, occurrences, adjustments, manual_rows, module_titles, *, now=None):
    """Only unique authored matches qualify; occurrence corrections take priority.

    All live components in the supplied modules must be included, even for a
    single-card request, so narrowing a read cannot hide an ambiguous match.
    """
    now = now or timezone.now()
    matched = _matched_occurrences(components, occurrences)
    counts = Counter((c['module_catalogue_id'], str(matched[c['id']]['session_id']))
                     for c in components if c['id'] in matched)
    corrections = {(r.source, str(r.source_id)): r for r in adjustments}
    identities = {}
    result = {}
    for component in components:
        key = (str(component['id']), str(component['module_catalogue_id']))
        settings = _settings(component)
        legacy = settings.get('legacySettings') or {}
        legacy = legacy if isinstance(legacy, dict) else {}
        row = matched.get(component['id'])
        if row:
            if counts[(component['module_catalogue_id'], str(row['session_id']))] != 1:
                continue
            end = _aware(row.get('scheduled_end'))
            if not end or end > now or any(str(row.get(k) or '').strip().lower() in INVALID
                                          for k in ('status', 'session_status', 'occurrence_status')):
                continue
            day = str(row['session_date'])
            correction = corrections.get(('microsoft-teams', str(row['session_id'])))
            if correction is not None and (correction.is_deleted or correction.status in {'present', 'absent'}):
                result[key] = (not correction.is_deleted and correction.status == 'present', correction.updated_at)
        else:
            # A broken/ambiguous Teams link must never fall back to a title.
            if any(settings.get(k) or legacy.get(k) for k in ('teamsOccurrenceId', 'teamsLiveSessionId')):
                continue
            day = str(settings.get('sessionDate') or legacy.get('sessionDate') or '')[:10]
            try:
                if date.fromisoformat(day) > now.astimezone(UK).date():
                    continue
            except ValueError:
                continue
        identity = (_text(module_titles.get(component['module_catalogue_id'])), _text(component['title']), day)
        if all(identity):
            identities.setdefault(identity, []).append(key)
    records = {}
    for record in manual_rows:
        identity = (_text(record.module_name), _text(record.session_title), str(record.session_date))
        records.setdefault(identity, []).append(record)
    # A coach correction to a KBC row has no authored id. Apply the same strict
    # module/title/day match as a free-form manual record, never a date alone.
    for record in adjustments:
        if record.source == 'kbc-attendance' and record.status in {'present', 'absent'} and not record.is_deleted:
            identity = (_text(record.module_name), _text(record.session_title), str(record.session_date))
            records.setdefault(identity, []).append(record)
    for identity, keys in identities.items():
        rows = records.get(identity, [])
        if len(keys) == 1 and len(rows) == 1 and keys[0] not in result:
            record = rows[0]
            result[keys[0]] = (record.status == 'present', record.updated_at)
    return result


def _read_manual_context(candidates, profile):
    from coach_api.models import CoachAttendanceSourceAdjustment, CoachManualAttendance
    from curriculum_api.models import ModuleAuthoringComponent, ModuleAuthoringModule
    from .attendance_lectures import ATTENDANCE_SOURCE_FIELDS, read_native_occurrences

    adjustments = list(CoachAttendanceSourceAdjustment.objects.filter(learner_id=profile.pk))
    manual = list(CoachManualAttendance.objects.filter(learner_id=profile.pk))
    if not adjustments and not manual:
        return {}, [], []
    module_ids = sorted({c['moduleId'] for c in candidates})
    source = SimpleNamespace(id=profile.enrolment_id, email=profile.email, username='')
    if manual or any(r.source == 'kbc-attendance' for r in adjustments):
        from .learner_detail import SOURCE_MODELS
        from .attendance_assignments import assigned_module_ids
        # Free-text records have no module id. Include the entire assigned plan
        # so opening one card cannot disambiguate two identically named modules.
        source = SOURCE_MODELS['commercial'].all_learners.filter(pk=profile.enrolment_id).only(*ATTENDANCE_SOURCE_FIELDS).first()
        if source is None:
            return {}, [], []
        module_ids = sorted(set(module_ids) | set(assigned_module_ids(source)))
    modules = dict(ModuleAuthoringModule.objects.using('enrolment').filter(
        module_catalogue_id__in=module_ids, deleted_at__isnull=True,
        is_programme_deleted=False).values_list('module_catalogue_id', 'title'))
    components = list(ModuleAuthoringComponent.objects.using('enrolment').filter(
        module_catalogue_id__in=modules, type__in=['live_session', 'live-session'],
        deleted_at__isnull=True, is_programme_deleted=False,
    ).values('id', 'module_catalogue_id', 'title', 'settings_json', 'expected_otjh'))
    occurrences = read_native_occurrences(source, module_ids=list(modules)) if modules else []
    return project_manual_completion(components, occurrences, adjustments, manual, modules), components, occurrences


def read_manual_completion(candidates, profile):
    return _read_manual_context(candidates, profile)[0]


def project_manual_credits(candidates, states, components, occurrences):
    """Use authored OTJ hours, never the meeting duration or fabricated evidence."""
    wanted = {(str(c['componentId']), str(c['moduleId'])) for c in candidates}
    matched = _matched_occurrences(components, occurrences)
    credits = {}
    for component in components:
        key = (str(component['id']), str(component['module_catalogue_id']))
        present, ended = states.get(key, (False, None))
        if key not in wanted or not present:
            continue
        try:
            hours = float(component.get('expected_otjh') or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if not isfinite(hours) or hours < 0:
            continue
        row = matched.get(component['id'], {})
        settings = _settings(component)
        legacy = settings.get('legacySettings') or {}
        legacy = legacy if isinstance(legacy, dict) else {}
        credits[key] = {
            'componentId': key[0], 'moduleId': key[1], 'title': component['title'],
            'occurrenceId': str(row.get('attendance_source_occurrence_id') or row.get('session_id') or ''),
            'sessionDate': str(row.get('session_date') or settings.get('sessionDate') or legacy.get('sessionDate') or '')[:10],
            'hours': hours, 'submittedAt': ended.isoformat() if ended else None,
            'timeTrackingSource': 'manual_attendance',
        }
    return list(credits.values())


def read_manual_credit_evidence(candidates, profile):
    states, components, occurrences = _read_manual_context(candidates, profile)
    return states, project_manual_credits(candidates, states, components, occurrences)
