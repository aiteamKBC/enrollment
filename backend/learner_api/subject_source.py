"""Current legacy subjects, read through the LMS without importing database rows.

Page hints only route requests. Exact source IDs AND the enrolment email must
match before records can enter the private cache or reach the learner.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import gzip
import hashlib
from http.client import HTTPException
import json
import re
import threading
from time import monotonic
from urllib.parse import urlsplit
import urllib.request
import logging

from django.conf import settings

from audit_api.last_audit_ledger_views import _activity_payload, _is_completed
from old_otjh.live_attempts import hints
from .subject_content import _SameHostRedirect
from .subject_dates import activity_schedule, apply_section_placement, as_date
from .student_activity_data import ITEM_FIELDS

_cache = OrderedDict()
_lock = threading.RLock()
_gates = [threading.Lock() for _ in range(32)]
TTL = 300
FAILURE_TTL = 30
MAX_CACHE_BYTES = 64 * 1024 * 1024
logger = logging.getLogger(__name__)

def _endpoint_label(endpoint):
    parsed = urlsplit(str(endpoint or ''))
    return f'{parsed.hostname or ""}{parsed.path}'


def _remember(key, fetch):
    """Bound private data in memory and coalesce identical concurrent requests."""
    with _gates[hash(key) % len(_gates)]:
        now = monotonic()
        with _lock:
            prior = _cache.get(key)
            if prior and prior[0] > now:
                _cache.move_to_end(key)
                return deepcopy(prior[2])
        try:
            value = fetch()
        except (OSError, HTTPException, ValueError, TypeError, KeyError, EOFError):
            now = monotonic()
            # A short source outage must not replace a verified full course with
            # an older, smaller mirror. Never retain a snapshot beyond an hour.
            if prior and prior[3] + 3600 > now:
                with _lock:
                    _cache[key] = (now + 30, prior[1], prior[2], prior[3])
                return deepcopy(prior[2])
            value = None
        size = len(json.dumps(value, default=str)) if value is not None else 0
        # Start freshness/backoff when the slow read finishes. Starting it
        # before a 15-second timeout made the old five-second failure cache expire
        # before insertion, so every queued page retried the failing source.
        now = monotonic()
        with _lock:
            _cache.pop(key, None)
            while _cache and (len(_cache) >= 128 or sum(v[1] for v in _cache.values()) + size > MAX_CACHE_BYTES):
                _cache.popitem(last=False)
            if size <= MAX_CACHE_BYTES:
                _cache[key] = (now + (TTL if value is not None else FAILURE_TTL), size, value, now)
        return deepcopy(value)


def _page(endpoint, secret, page):
    started = monotonic()
    label = _endpoint_label(endpoint)
    request = urllib.request.Request(f'{endpoint}?page={page}&per_page=20', headers={
        'X-KBC-API-Key': secret, 'Accept': 'application/json',
        'Accept-Encoding': 'gzip', 'User-Agent': 'KBC-LearningOS/1.0',
    })
    # Full source pages are large; the live audit observed valid responses
    # exceeding 15 seconds. Match the bounded timeout used by that audit.
    try:
        with urllib.request.build_opener(_SameHostRedirect()).open(request, timeout=45) as response:
            stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
            raw = stream.read(80 * 1024 * 1024 + 1)
            logger.info('learner_live_source stage=source_request result=success page=%s http_status=%s endpoint=%s elapsed_ms=%d', page, getattr(response, 'status', None), label, int((monotonic() - started) * 1000))
    except Exception as exc:
        logger.warning('learner_live_source stage=source_request result=failed page=%s http_status=%s exception=%s endpoint=%s elapsed_ms=%d', page, getattr(exc, 'code', None), type(exc).__name__, label, int((monotonic() - started) * 1000))
        raise
    if len(raw) > 80 * 1024 * 1024:
        raise ValueError('Source response exceeds limit')
    try:
        payload = json.loads(raw)
    except Exception as exc:
        logger.warning('learner_live_source stage=schema_validation result=invalid exception=%s endpoint=%s page=%s elapsed_ms=%d', type(exc).__name__, label, page, int((monotonic() - started) * 1000))
        raise
    if not isinstance(payload, dict):
        logger.warning('learner_live_source stage=schema_validation result=invalid exception=ValueError endpoint=%s page=%s elapsed_ms=%d', label, page, int((monotonic() - started) * 1000))
        raise ValueError('Invalid source response')
    pg = payload.get('pagination') or {}
    if pg.get('page') != page or pg.get('per_page') != 20 or not isinstance(payload.get('groups'), list):
        logger.warning('learner_live_source stage=schema_validation result=invalid exception=ValueError endpoint=%s page=%s elapsed_ms=%d', label, page, int((monotonic() - started) * 1000))
        raise ValueError('Source pagination was not honoured')
    logger.info('learner_live_source stage=schema_validation result=success endpoint=%s page=%s elapsed_ms=%d', label, page, int((monotonic() - started) * 1000))
    return payload


def _unique_activities(definitions):
    """One learner/course/activity result can have several lecture placements.

    Keep its content and completion once. Conflicting lecture dates are not a
    reliable activity date; conflicting quiz IDs must not choose an answer key.
    """
    unique = {}
    for incoming in definitions:
        aid = incoming.get('activity_id')
        if type(aid) is not int:
            raise ValueError('Invalid source activity identities')
        if aid not in unique:
            unique[aid] = deepcopy(incoming)
            continue
        existing = unique[aid]
        if existing.get('activity_date') != incoming.get('activity_date'):
            existing['activity_date'] = None
        old_quiz, new_quiz = existing.get('quiz') or {}, incoming.get('quiz') or {}
        old_id, new_id = old_quiz.get('quiz_id'), new_quiz.get('quiz_id')
        if old_id and new_id and old_id != new_id:
            existing['quiz_definition_ambiguous'] = True
        elif not old_id and new_id:
            existing['quiz'] = deepcopy(new_quiz)
        for field in ('video', 'audio', 'reading'):
            if not existing.get(field) and incoming.get(field):
                existing[field] = deepcopy(incoming[field])
    return list(unique.values())


def _select(payload, ident, email):
    groups, found = [], False
    allowed_emails = {email} if isinstance(email, str) else set(email)
    for group in payload['groups']:
        if not isinstance(group, dict) or not isinstance(group.get('learners'), list):
            raise ValueError('Invalid source memberships')
        if any(not isinstance(learner, dict) for learner in group['learners']):
            raise ValueError('Invalid source learner')
        matches = [learner for learner in group['learners'] if learner.get('learner_id') == ident]
        if not matches:
            continue
        if len(matches) != 1 or str(matches[0].get('learner_email') or '').strip().casefold() not in allowed_emails:
            raise ValueError('Source identity mismatch')
        found = True
        definitions = group.get('activities')
        results = matches[0].get('activity_results')
        if type(group.get('group_id')) is not int or group['group_id'] <= 0 or not isinstance(definitions, list) or not isinstance(results, list):
            raise ValueError('Incomplete source course')
        if any(not isinstance(row, dict) for row in [*definitions, *results]):
            raise ValueError('Invalid source activity records')
        groups.append({'id': group['group_id'], 'name': group.get('group_name') or '',
                       'activities': _unique_activities(definitions), 'results': results})
        if not isinstance(email, str):
            groups[-1]['_learner_email'] = str(matches[0]['learner_email']).strip().casefold()
    if not found:
        logger.info('learner_live_source stage=learner_match result=no-match source_learner_id=%s id_match=no email_match=no', ident)
        return None
    logger.info('learner_live_source stage=learner_match result=success source_learner_id=%s id_match=yes email_match=yes membership_groups=%s', ident, len(groups))
    logger.info('learner_live_source stage=membership_match result=success source_learner_id=%s matching_groups=%s', ident, len(groups))
    return groups


def _read_identity(endpoint, secret, ident, email, pages):
    routing = hints()
    page = routing.get(str(ident))
    if not page:
        # New aliases can reuse a nearby routing hint; it never grants access.
        nearest = min((int(key) for key in routing), key=lambda key: abs(key - ident), default=None)
        page = routing.get(str(nearest), 1)
    for candidate in dict.fromkeys((page, max(1, page - 1), page + 1)):
        if candidate not in pages:
            pages[candidate] = _page(endpoint, secret, candidate)
        selected = _select(pages[candidate], ident, email)
        if selected is not None:
            return selected
    raise ValueError('Source learner not found near routing hint')


def read_learner(cursor, aptem_id, email):
    """Return only courses belonging to a DB-verified source identity."""
    email = str(email or '').strip().casefold()
    secret = getattr(settings, 'KBC_LMS_API_KEY', '')
    endpoint = getattr(settings, 'KBC_LMS_SCHEMA_URL', '')
    label = _endpoint_label(endpoint)
    if not email or not secret or not endpoint:
        logger.warning('learner_live_source stage=configuration result=failed aptem_id=%s endpoint_configured=%s api_key_configured=%s', aptem_id, bool(endpoint), bool(secret))
        return None
    cursor.execute('''SELECT l.id,i.source_learner_id,
        coalesce(nullif(i.source_payload->>'source_email',''),i.source_payload->>'learner_email'),
        coalesce((i.source_payload#>>'{legacy_alias,is_primary}')::boolean,
                 i.source_payload ? 'record_id') AS is_primary
        FROM "Learner".learners l
        JOIN "Learner".learner_external_identities i
          ON i.learner_id=l.id AND i.source_system='old_lms' AND i.deleted_at IS NULL
        WHERE l.aptem_id=%s AND lower(btrim(l.email))=%s
        UNION ALL
        SELECT l.learner_id,coalesce(a.lms_learner_id,l.learner_id)::text,
        CASE WHEN a.lms_learner_id IS NULL THEN l.learner_email ELSE a.lms_email END,
        coalesce(a.is_primary,true)
        FROM "Last_audit".learners l
        LEFT JOIN "Last_audit".learner_lms_aliases a
          ON a.aptem_id=l.aptem_id AND a.canonical_lms_id=l.learner_id
        WHERE l.aptem_id=%s AND lower(btrim(l.learner_email))=%s
          AND NOT EXISTS (SELECT 1 FROM "Learner".learners canonical
                          WHERE canonical.aptem_id=l.aptem_id)''',
        [aptem_id, email, aptem_id, email])
    identities = cursor.fetchall()
    if not identities:
        logger.warning('learner_live_source stage=database_identity result=no-match aptem_id=%s aliases=0', aptem_id)
        return None
    if len({row[0] for row in identities}) != 1:
        logger.warning('learner_live_source stage=database_identity result=invalid aptem_id=%s reason=multiple_canonical_rows aliases=%s', aptem_id, len(identities))
        return None
    if any(not str(row[1]).isdigit() or int(row[1]) <= 0 for row in identities):
        return None
    sources = tuple(sorted({(int(row[1]), str(row[2] or '').strip().casefold()) for row in identities}))
    # The enrolment email verifies the canonical learner above. Each stored
    # alias then verifies its own original email, which can be a former account.
    if any(not source_email for _, source_email in sources):
        logger.warning('learner_live_source stage=database_identity result=invalid aptem_id=%s reason=missing_alias_email aliases=%s', aptem_id, len(sources))
        return None
    if len({ident for ident, _ in sources}) != len(sources):
        logger.warning('learner_live_source stage=database_identity result=invalid aptem_id=%s reason=duplicate_source_ids aliases=%s', aptem_id, len(sources))
        return None
    primary_ids = tuple(sorted(int(row[1]) for row in identities if row[3] is True))
    logger.info('learner_live_source stage=database_identity result=success aptem_id=%s aliases=%s endpoint=%s', aptem_id, len(sources), label)
    key = ('learner', endpoint, hashlib.sha256(secret.encode()).hexdigest(), sources, email, primary_ids)
    def fetch():
        pages, groups = {}, {}
        for ident, source_email in sources:
            # A primary account can use either its saved original email or its
            # current canonical email; both are already verified DB identities.
            expected = tuple(sorted({source_email, email})) if ident in primary_ids and source_email != email else source_email
            for group in _read_identity(endpoint, secret, ident, expected, pages):
                existing = groups.get(group['id'])
                if existing:
                    # Aliases share course definitions; results preserve every
                    # positive completion and the best attempt on each activity.
                    if {a['activity_id'] for a in existing['activities']} != {a['activity_id'] for a in group['activities']}:
                        raise ValueError('Course changed during source read')
                    existing['results'].extend(group['results'])
                else:
                    groups[group['id']] = {**group, '_learner_id': ident, '_learner_email': group.get('_learner_email', source_email)}
        return {'groups': list(groups.values())}
    result = _remember(key, fetch)
    if result is None:
        logger.warning('learner_live_source stage=final_result result=historical_fallback aptem_id=%s endpoint=%s', aptem_id, label)
    else:
        logger.info('learner_live_source stage=final_result result=success aptem_id=%s groups=%s endpoint=%s', aptem_id, len(result.get('groups') or []), label)
    return result


def _section_dates(sections):
    dates = [as_date(activity_schedule(s['title'])['date']) for s in sections]
    output = {}
    for index, section in enumerate(sections):
        title = section['title']
        schedule_title = title
        # Only recover a missing year when explicit surrounding lecture dates
        # bound one possible date. Never borrow a cloned upload timestamp.
        partial = re.search(r'(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})\s*$', title) if dates[index] is None else None
        before = next((d for d in reversed(dates[:index]) if d), None)
        after = next((d for d in dates[index + 1:] if d), None)
        if partial and before and after and before <= after and (after - before).days <= 366:
            candidates = []
            for year in range(before.year, after.year + 1):
                try:
                    candidate = date(year, int(partial[2]), int(partial[1]))
                    if before <= candidate <= after:
                        candidates.append(candidate)
                except ValueError:
                    pass
            if len(candidates) == 1:
                schedule_title = title[:partial.start()] + candidates[0].isoformat()
        for aid in section['ids']:
            context = {'section_title': title, 'schedule_title': schedule_title,
                       'source_section_id': section.get('id')}
            if aid in output and output[aid] != context:
                output[aid] = {'ambiguous': True}
            else:
                output[aid] = context
    return output


def _course_sections(payload, group_id, learner_id, email):
    """Extract only the exact learner/course's current curriculum placement.

    This endpoint includes both standalone quizzes and reading companions. Their
    source IDs are used solely for scheduling; they never add another activity.
    """
    if not isinstance(payload, list):
        raise ValueError('Invalid course schedule response')
    matches = [row for row in payload if isinstance(row, dict)
               and row.get('user_id') == learner_id and row.get('course_id') == group_id]
    if len(matches) != 1 or str(matches[0].get('email') or '').strip().casefold() != email:
        raise ValueError('Course schedule identity mismatch')
    course = matches[0]
    if not isinstance(course.get('materials'), list) or not isinstance(course.get('quizzes'), list):
        raise ValueError('Incomplete course schedule')
    sections = {}
    for row in [*course['materials'], *course['quizzes']]:
        if not isinstance(row, dict) or type(row.get('component_id')) is not int:
            raise ValueError('Invalid scheduled activity')
        if row.get('section_id') is None:
            continue
        section = {'id': str(row['section_id']), 'title': str(row.get('section_title') or ''), 'order': int(row['section_order']), 'ids': []}
        existing = sections.setdefault(str(row['section_id']), section)
        if (existing['title'], existing['order']) != (section['title'], section['order']):
            raise ValueError('Conflicting section records')
        existing['ids'].append(row['component_id'])
    ordered = sorted(sections.values(), key=lambda section: section['order'])
    if len({section['order'] for section in ordered}) != len(ordered):
        raise ValueError('Ambiguous section order')
    return ordered


def course_schedule(group_id, learner_id, email):
    origin = urlsplit(getattr(settings, 'KBC_LMS_SCHEMA_URL', ''))
    secret = getattr(settings, 'KBC_LMS_API_KEY', '')
    if not origin.netloc or not secret or not learner_id or not email:
        return {}
    address = (f'{origin.scheme}://{origin.netloc}/wp-json/custom/v1/courses-progress'
               f'?user_id={int(learner_id)}&course_id={int(group_id)}'
               '&include_answers=false&include_other_modules=false')
    def fetch():
        request = urllib.request.Request(address, headers={
            'User-Agent': 'KBC-LearningOS/1.0', 'Accept': 'application/json',
            'Accept-Encoding': 'gzip', 'X-KBC-API-Key': secret,
        })
        with urllib.request.build_opener(_SameHostRedirect()).open(request, timeout=10) as response:
            stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
            body = stream.read(16 * 1024 * 1024 + 1)
        if len(body) > 16 * 1024 * 1024:
            raise ValueError('Course schedule exceeds limit')
        sections = _course_sections(json.loads(body), group_id, learner_id, email)
        return _section_dates(sections)
    return _remember(('schedule', address, email, hashlib.sha256(secret.encode()).hexdigest()), fetch) or {}


def _number(value):
    try:
        number = float(value)
        return number if 0 <= number < float('inf') else None
    except (ValueError, TypeError):
        return None


def merge_result(before, incoming):
    """Same positive completion / highest-score policy as persisted attempts."""
    merged = {**before, **incoming}
    ranks = {'completed': 3, 'quiz_attempted': 2, 'reading_viewed': 1}
    if ranks.get(before.get('status'), 0) > ranks.get(incoming.get('status'), 0):
        merged['status'] = before['status']
    for flag in ('video_started', 'video_completed', 'reading_viewed', 'quiz_attempted', 'quiz_passed'):
        merged[flag] = before.get(flag) is True or incoming.get(flag) is True
    def percent(row):
        score, maximum = _number(row.get('quiz_score')), _number(row.get('quiz_maximum_score'))
        return score / maximum if score is not None and maximum else None
    old, new = percent(before), percent(incoming)
    if old is not None and (new is None or old > new or (old == new and before.get('quiz_answers') and not incoming.get('quiz_answers'))):
        for key in ('quiz_score', 'quiz_maximum_score', 'quiz_attempt_number', 'quiz_answers'):
            merged[key] = before.get(key)
    return merged


def source_rows(group):
    definitions = {a['activity_id']: a for a in _unique_activities(group['activities'])}
    results = {}
    for result in group['results']:
        aid = result.get('activity_id')
        if aid not in definitions:
            continue
        video, reading, quiz = result.get('video_result') or {}, result.get('reading_result') or {}, result.get('quiz_result') or {}
        incoming = {'status': result.get('status'), 'video_started': video.get('started'), 'video_completed': video.get('completed'),
                    'reading_viewed': reading.get('viewed'), 'quiz_attempted': quiz.get('attempted'), 'quiz_passed': quiz.get('passed'),
                    'quiz_score': quiz.get('score'), 'quiz_maximum_score': quiz.get('maximum_score'),
                    'quiz_attempt_number': quiz.get('attempt_number'), 'quiz_answers': quiz.get('answers') or []}
        results[aid] = merge_result(results.get(aid, {}), incoming)
    rows = []
    for position, a in enumerate(definitions.values()):
        video, audio, reading, quiz = a.get('video') or {}, a.get('audio') or {}, a.get('reading') or {}, a.get('quiz') or {}
        result = results.get(a['activity_id'], {})
        rows.append({'activity_id': a['activity_id'], 'group_id': group['id'], 'group_name': group['name'],
                     'activity_type': a.get('activity_type'), 'title': a.get('title'), 'activity_date': as_date(a.get('activity_date')),
                     'position': position, 'has_result': bool(result), 'video_iframe_url': video.get('iframe_url'),
                     'audio_url': audio.get('iframe_url'), 'reading_iframe_url': reading.get('iframe_url'),
                     'reading_text_body': reading.get('text_body'), 'reading_type': reading.get('reading_type'),
                     'quiz_id': quiz.get('quiz_id'), 'quiz_questions': quiz.get('questions'), 'quiz_body': quiz.get('quiz_body'),
                     'quiz_definition_ambiguous': bool(a.get('quiz_definition_ambiguous')),
                     'quiz_passing_score': quiz.get('passing_score'), 'quiz_maximum_score': result.get('quiz_maximum_score') or quiz.get('maximum_score'),
                     **{k: v for k, v in result.items() if k != 'quiz_maximum_score'}})
    return rows


def overlay_subjects(payload, live, stored_schedules):
    if live is None:
        return payload
    previous = {(a['group_id'], a['source_activity_id']): a for a in payload['activities']}
    groups = live['groups']
    with ThreadPoolExecutor(max_workers=4) as pool:
        schedules = dict(zip([g['id'] for g in groups], pool.map(
            lambda group: course_schedule(group['id'], group.get('_learner_id'), group.get('_learner_email')), groups)))
    replaced = {g['id'] for g in groups}
    items = [a for a in payload['activities'] if a['group_id'] not in replaced]
    subjects = {s['id']: s for s in payload.get('subjects', [])}
    for group in groups:
        subjects[group['id']] = {'id': group['id'], 'name': group['name']}
        for row in source_rows(group):
            key = (group['id'], row['activity_id'])
            old = previous.get(key, {})
            row.update(aptem_id=payload['aptem_id'], learner_id=0)
            normalized = _activity_payload(row)
            item = {k: normalized[k] for k in ITEM_FIELDS}
            # Keep the audit's OTJ hours and later local/source completion.
            for field in ('actual', 'planned', 'hours_mapped', 'planned_hours_mapped'):
                item[field] = old.get(field, False if field.endswith('mapped') else 0)
            item['completed'] |= bool(old.get('completed'))
            best = merge_result(old, item)
            for field in ('quiz_score', 'quiz_maximum_score', 'status'):
                item[field] = best.get(field)
            context = schedules[group['id']].get(row['activity_id']) or {}
            stored = stored_schedules.get(key, {})
            manual_week = stored.get('section_source') == 'builder_section_title' and stored.get('section_title') != stored.get('exported_section_title')
            title = stored.get('section_title') if manual_week else context.get('schedule_title') or stored.get('section_title')
            original = stored.get('original_created_at')
            item.update(activity_schedule(row['title'], row['activity_date'], original,
                        section_title=title, section_source='builder_section_title' if manual_week else 'section_title'))
            if context.get('ambiguous') and not manual_week:
                item.update(activity_schedule(row['title']))
            item.update(position=row['position'], has_result=row['has_result'],
                        section_title=stored.get('section_title') if manual_week else context.get('section_title') or stored.get('section_title') or '')
            # A current source placement wins over an older exported section.
            section_id = context.get('source_section_id') if context else stored.get('source_section_id')
            apply_section_placement(item, group['id'], section_id)
            items.append(item)
    return {**payload, 'subjects': list(subjects.values()), 'activities': items}


def overlay_progress_rows(historical, live):
    """Use the same current course inventory for dashboard totals and cards.

    This only reconciles result facts; it does not fetch lecture schedules or
    content and retains existing audited mappings for each exact placement.
    """
    if live is None:
        return historical
    previous = {(r['group_id'], r['activity_id']): r for r in historical}
    replaced = {group['id'] for group in live['groups']}
    result = [r for r in historical if r['group_id'] not in replaced]
    for group in live['groups']:
        for row in source_rows(group):
            old = previous.get((row['group_id'], row['activity_id']), {})
            combined = {**old, **row}
            if _is_completed(old):
                combined['status'] = 'completed'
            result.append(combined)
    return result


def material(live, group_id, activity_id, stored, learner_name):
    if live is None:
        return stored
    group = next((g for g in live['groups'] if g['id'] == group_id), None)
    if group is None:
        return stored
    row = next((r for r in source_rows(group) if r['activity_id'] == activity_id), None)
    if row is None:
        return None
    previous = dict((stored or {}).get('_source', {}))
    if previous.get('result_maximum_score') is not None:
        previous['quiz_maximum_score'] = previous['result_maximum_score']
    row = merge_result(previous, row)
    row['result_maximum_score'] = row.get('quiz_maximum_score')
    row['learner_name'] = learner_name
    return {'title': row['title'], 'learner_name': learner_name, 'video_url': row.get('video_iframe_url'),
            'audio_url': row.get('audio_url'), 'reading_url': row.get('reading_iframe_url'),
            'reading_html': row.get('reading_text_body'), '_source': row}
