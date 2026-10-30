"""Read-only material resolution for an already authorized monthly report.

The arranged ledger is the source of identity. Material snapshots supply the
old LMS's embed contract; quiz answers always come from this learner and group.
No ingestion, table creation, backfill, or name-based material matching here.
"""
import hashlib
import html as html_entities
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin, urlencode, parse_qs

from django.conf import settings

MEDIA = {'video', 'audio', 'reading+quiz'}
ORIGIN = 'https://kentbusinesscollege.org'
_cache = {}
_cache_lock = threading.Lock()
_auth_failure = None
_preview_cache = {}
_backup_cache = {}


def obj(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def values(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def http_url(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username and not parsed.password:
            return value.strip()
    except ValueError:
        pass
    return None


def source_ids(row):
    if str(row.get('category', '')).lower() not in MEDIA:
        return row.get('group_id'), []
    ref = row.get('source_ref') or ''
    if re.fullmatch(r'rq:[0-9]+(?::[0-9]+)+', ref):
        group, *ids = map(int, ref.split(':')[1:])
        return group, list(dict.fromkeys(ids))
    if re.fullmatch(r'la:[0-9]+:[0-9]+', ref):
        _, group, ident = ref.split(':')
        return int(group), [int(ident)]
    return row.get('group_id'), [row['activity_id']] if row.get('activity_id') else []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never send the source API key to a redirect target.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def live_material(ident):
    """Read a missing definition, with a short bounded cache and auth backoff."""
    global _auth_failure
    key = getattr(settings, 'KBC_LMS_API_KEY', '')
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    now = time.monotonic()
    cache_key = (fingerprint, ident)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
        if not key or (_auth_failure and _auth_failure[0] == fingerprint and _auth_failure[1] > now):
            return {'error': 'source_authentication'}
    request = urllib.request.Request(f'{ORIGIN}/wp-json/kbc-lms/v1/material/{int(ident)}/schema',
        headers={'X-KBC-API-Key': key, 'Accept': 'application/json', 'User-Agent': 'KBC-Record-Review/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=6) as response:
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('Material response is too large')
            payload = obj(json.loads(data))
        material = obj(payload.get('material')) or payload
        if str(material.get('material_id')) != str(ident):
            result = {'error': 'source_unavailable'}
        else:
            result = material
    except urllib.error.HTTPError as error:
        result = {'error': 'source_authentication' if error.code in (401, 403) else 'source_unavailable'}
        if error.code in (401, 403):
            with _cache_lock:
                _auth_failure = (fingerprint, now + 60)
    except (OSError, ValueError):
        result = {'error': 'source_unavailable'}
    with _cache_lock:
        if len(_cache) >= 2048:
            _cache.clear()
        _cache[cache_key] = (now + (15 if 'error' in result else 30), result)
    return result


def catalogue(ids):
    from .repository import source_query
    if not ids:
        return {}
    definitions = source_query('''SELECT activity_id, title, activity_type, video_iframe_url,
        reading_iframe_url, reading_type, reading_text_body,
        raw #>> '{audio,iframe_url}' AS audio_iframe_url,
        quiz_id, quiz_body, quiz_questions FROM "Last_audit".activities
        WHERE activity_id=ANY(%s)''', [list(ids)])
    snapshots = source_query('''SELECT material_id, title, payload, source_url, backup_status,
        blob_container, blob_name, blob_content_type, blob_size_bytes, updated_at FROM
        structured_manual_activities.lms_material_snapshots WHERE material_id=ANY(%s)''', [list(ids)])
    result = {row['activity_id']: row for row in definitions}
    for snapshot in snapshots:
        item = result.setdefault(snapshot['material_id'], {'activity_id': snapshot['material_id']})
        item['material'] = {
            **obj(snapshot['payload']), '_source_url': snapshot.get('source_url')}
        if snapshot.get('backup_status') == 'available' and snapshot.get('blob_name') and snapshot.get('blob_container') and (snapshot.get('blob_size_bytes') or 0) > 0:
            item['backup'] = snapshot
    return result


def display_content(item):
    from audit_api.last_audit_ledger_views import _activity_content_url
    material = obj(item.get('material'))
    source = obj(material.get('source'))
    # Quiz permalinks can lead to the WordPress login and cannot be embedded.
    # Render the actual learner's saved attempt locally instead.
    is_quiz_link = material.get('content_type') == 'quiz' or source.get('requires_lms_login') is True
    # Live shared-table edits take precedence over the older material snapshot.
    url = http_url(_activity_content_url(item.get('video_iframe_url'), item.get('reading_iframe_url'),
                                         item.get('reading_type'), item.get('audio_iframe_url')))
    url = url or (None if is_quiz_link else http_url(material.get('iframe_url') or material.get('_source_url')))
    if url and (material.get('content_type') or item.get('reading_type') or '').lower() == 'pdf':
        parsed = urlparse(url)
        if parsed.hostname == 'view.officeapps.live.com':
            # The signed /material/<id>/view endpoint has no .pdf extension.
            # Office cannot render PDF files; use their actual PDF response.
            url = http_url((parse_qs(parsed.query).get('src') or [None])[0]) or url
    html = item.get('reading_text_body') or material.get('text_body') or None
    html = html if meaningful_html(html) else None
    return url, html


def meaningful_html(value):
    if not isinstance(value, str):
        return False
    body = re.sub(r'<(script|style|head)\b[^>]*>.*?</\1>|<!--.*?-->', '', value, flags=re.I | re.S)
    text = html_entities.unescape(re.sub(r'<[^>]+>', '', body)).strip()
    return bool(text or re.search(r'<(?:img|iframe|audio|video|object|embed)\b[^>]*(?:src|data)\s*=', body, re.I))


def quiz_definition_payload(questions, description):
    """Expose authored questions separately from any student's graded attempt.

    The source schema also includes solutions. Those are not learner answers
    and must never be used to manufacture a completed attempt.
    """
    result = []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            continue
        body = question.get('question_body') or question.get('question_text')
        if not meaningful_html(body):
            continue
        result.append({'question_id': question.get('question_id') or index,
            'question_order': question.get('question_order') or index,
            'question_text': body,
            'answer_options': [{'option_text': option.get('option_body') or option.get('option_text') or ''}
                for option in values(question.get('options')) if isinstance(option, dict)]})
    return {'description': description, 'questions': result} if result else None


def resolve(learner, rows, *, companions=False, refresh_missing=True):
    """Batch all definitions/answers; expose no other learner's records."""
    from . import repository as repo
    from audit_api.last_audit_ledger_views import _quiz_attempt_payload
    identities = {row['id']: source_ids(row) for row in rows}
    ids = {ident for _, row_ids in identities.values() for ident in row_ids}
    definitions = catalogue(ids)
    attempts = repo.source_query('''SELECT activity_id, group_id, quiz_attempted, quiz_passed,
        quiz_score, quiz_maximum_score, quiz_attempt_number, quiz_answers
        FROM "Last_audit".activity_results WHERE learner_id=%s AND activity_id=ANY(%s)''',
        [learner.get('lms_id'), list(ids)]) if ids and learner.get('lms_id') else []
    answers = {(a['group_id'], a['activity_id']): a for a in attempts}
    from .retained_content import read as read_retained
    # Only retrieve the small, relevant part of the original learner JSON.
    recovery_ids = {ident for ident in ids if not any(display_content(definitions.get(ident, {})))
                    or urlparse(display_content(definitions.get(ident, {}))[0] or '').hostname == 'kentbusinesscollege.org'}
    retained = read_retained(learner, recovery_ids)
    missing = set()
    for row in rows:
        group, row_ids = identities[row['id']]
        for ident in row_ids:
            item = definitions.get(ident, {})
            attempt = answers.get((group, ident), {})
            saved = retained.get(ident, {})
            has_questions = bool(values(item.get('quiz_questions')) or values(obj(obj(item.get('material')).get('quiz')).get('questions'))
                or values(attempt.get('quiz_answers')) or saved.get('quiz'))
            if not any(display_content(item)) and not item.get('backup') and not saved.get('urls') and not meaningful_html(saved.get('html')) and not has_questions:
                missing.add(ident)
    if refresh_missing and missing:
        # Probe one first: an invalid shared key must not issue N identical 401s.
        first = next(iter(missing))
        live = {first: live_material(first)}
        if live[first].get('error') != 'source_authentication':
            with ThreadPoolExecutor(max_workers=4) as pool:
                others = sorted(missing - {first})
                live.update(zip(others, pool.map(live_material, others)))
        for ident, material in live.items():
            item = definitions.setdefault(ident, {'activity_id': ident})
            if 'error' in material:
                item['source_error'] = material['error']
            else:
                item['material'] = material
    if refresh_missing:
        from .live_attempts import read as read_live_attempts
        missing_attempts = set()
        for group, row_ids in identities.values():
            for ident in row_ids:
                item = definitions.get(ident, {})
                material = obj(item.get('material'))
                saved = retained.get(ident, {})
                is_quiz = item.get('quiz_id') or values(item.get('quiz_questions')) or material.get('content_type') == 'quiz'
                retained_matches = saved.get('quiz') and (not saved.get('groups') or str(group) in saved['groups'])
                if is_quiz and not values(answers.get((group, ident), {}).get('quiz_answers')) and not retained_matches:
                    missing_attempts.add((group, ident))
        if missing_attempts:
            answers.update(read_live_attempts(learner, missing_attempts))
    output = {}
    for row in rows:
        group, row_ids = identities[row['id']]
        parts = []
        for ident in row_ids:
            item = {**definitions.get(ident, {}), 'activity_id': ident}
            material = obj(item.get('material'))
            material_quiz = obj(material.get('quiz'))
            attempt = answers.get((group, ident), {})
            quiz_definition = values(item.get('quiz_questions')) or values(material_quiz.get('questions'))
            is_quiz = bool(item.get('quiz_id') or quiz_definition or values(attempt.get('quiz_answers')))
            title = item.get('title') or material.get('material_title') or row.get('title') or f'Activity {ident}'
            description = item.get('quiz_body') or material_quiz.get('quiz_body') or material_quiz.get('description')
            quiz = _quiz_attempt_payload({**item, **attempt, 'title': title, 'quiz_questions': quiz_definition,
                'quiz_body': description,
                'quiz_id': ident if is_quiz else None, 'aptem_id': learner['aptem_id']}, ident)
            url, html = display_content(item)
            saved = retained.get(ident, {})
            # A group-qualified record must agree. Never substitute another
            # group's attempt when the primary ledger explicitly identifies it.
            group_conflict = saved.get('groups') and str(group) not in saved['groups']
            other_group_attempt = not attempt and any(aid == ident for _, aid in answers)
            if not group_conflict:
                if not (quiz['attempt'] and quiz['attempt']['quiz_body']['questions']) and not other_group_attempt and saved.get('quiz'):
                    quiz = saved['quiz']
                    is_quiz = True
                html = html or (saved.get('html') if meaningful_html(saved.get('html')) else None)
            snapshot_url = display_content({'material': material})[0]
            candidates = list(dict.fromkeys(u for u in [url, snapshot_url, *(saved.get('urls', []) if not group_conflict else [])] if u))
            url = candidates[0] if candidates else None
            question_count = len(quiz['attempt']['quiz_body']['questions']) if quiz['attempt'] else 0
            # A result/score without any saved answers is not a reviewable
            # attempt. The original questions can still be previewed below.
            reviewable_quiz = question_count and (bool(values(attempt.get('quiz_answers')))
                or (not group_conflict and not other_group_attempt and bool(saved.get('quiz'))))
            quiz_payload = {'state': quiz['state'], 'attempt': quiz['attempt']} if is_quiz else None
            if quiz_payload is not None:
                definition = quiz_definition_payload(quiz_definition, description)
                if definition:
                    quiz_payload['definition'] = definition
                    quiz_payload['answers_available'] = bool(reviewable_quiz)
            backup = item.get('backup')
            available = bool(url or html or reviewable_quiz or backup)
            parts.append({'id': ident, 'title': title, 'category': item.get('activity_type') or row['category'],
                'url': url, 'html': html, 'quiz': quiz_payload,
                'content_type': 'application/pdf' if (material.get('content_type') or item.get('reading_type') or '').lower() == 'pdf' else None,
                'alternate_urls': candidates[1:],
                '_backup': backup,
                'document': backup_document(learner, row, backup) if backup else None,
                'available': available, 'issue': None if available else 'source_attempt_missing' if quiz_payload and quiz_payload.get('definition') else item.get('source_error', 'source_content_missing')})
        # Attendance is reviewed from its register record; assignments need their
        # linked evidence. Neither may accidentally resolve an unrelated LMS ID.
        if row['category'] == 'assignment':
            available = bool(row.get('documents'))
        elif row['category'] == 'attendance':
            available = True
        else:
            available = bool(parts) and all(part['available'] for part in parts)
        if companions and group is not None:
            parts = add_companions(group, parts)
            available = available and all(part['available'] for part in parts)
        output[row['id']] = {'parts': parts, 'available': available}
    return output


def backup_document(learner, row, backup):
    import mimetypes
    from pathlib import PurePosixPath
    ident = backup['material_id']
    extension = PurePosixPath(backup['blob_name']).suffix or mimetypes.guess_extension(backup.get('blob_content_type') or '') or ''
    title = backup.get('title') or row.get('title') or f'Material {ident}'
    name = title if title.lower().endswith(extension.lower()) else title + extension
    query = urlencode({'aptem_id': learner['aptem_id'], 'month': row.get('month', ''), 'v': str(backup.get('updated_at') or '')})
    return {'id': ident, 'display_name': name, 'content_type': backup.get('blob_content_type'),
            'url': f"/audit_api/old-otjh/material-documents/{row['id']}/{ident}/?{query}"}


def backup_available(backup):
    from learner_api import evidence_storage
    key = (backup['blob_container'], backup['blob_name'], str(backup.get('updated_at')))
    now = time.monotonic()
    with _cache_lock:
        cached = _backup_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    try:
        props = evidence_storage._service_client().get_blob_client(container=key[0], blob=key[1]).get_blob_properties(
            connection_timeout=5, read_timeout=5, retry_total=0)
        available = props.size > 0
    except Exception:
        available = False
    with _cache_lock:
        if len(_backup_cache) >= 2048:
            _backup_cache.clear()
        _backup_cache[key] = (now + (30 if available else 10), available)
    return available


def add_companions(group, parts):
    from . import repository as repo
    from audit_api.manual_ledger_views import _companion_reading_part
    existing = {p['id'] for p in parts}
    output = []
    for part in parts:
        if part['quiz'] and not part['url'] and not part['html']:
            # Honour explicitly arranged reading/quiz pairs in this same group.
            paired = repo.source_query('''SELECT reading_activity_id FROM structured_manual_activities.reading_quiz_pairs
                WHERE group_id=%s AND quiz_activity_id=%s ORDER BY id''', [group, part['id']])
            candidates = []
            if paired:
                ids = [p['reading_activity_id'] for p in paired if p['reading_activity_id'] not in existing]
                definitions = catalogue(ids)
                for ident in ids:
                    item = definitions.get(ident, {'activity_id': ident})
                    url, html = display_content(item)
                    if not url and not html:
                        material = live_material(ident)
                        url, html = display_content({'material': material})
                    candidates.append({'activity_id': ident, 'title': item.get('title') or f'Reading {ident}',
                                       'content_url': url, 'reading_text_body': html,
                                       'reading_type': obj(item.get('material')).get('content_type') or item.get('reading_type')})
            else:
                with repo.source_connection().cursor() as cursor:
                    companion = _companion_reading_part(cursor, group, part['id'], part['title'])
                if companion:
                    candidates.append(companion)
            for candidate in candidates:
                ident = candidate['activity_id']
                if ident not in existing:
                    url, html = display_content({'reading_iframe_url': candidate['content_url'],
                        'reading_text_body': candidate['reading_text_body'], 'reading_type': candidate.get('reading_type')})
                    available = bool(candidate['content_url'] or meaningful_html(candidate['reading_text_body']))
                    output.append({'id': ident, 'title': candidate['title'], 'category': 'reading',
                        'url': url, 'html': html, 'quiz': None,
                        'content_type': 'application/pdf' if candidate.get('reading_type') == 'pdf' else None,
                        'available': available, 'issue': None if available else 'source_content_missing'})
                    existing.add(ident)
        output.append(part)
    return output


def check_source(url, *, framed=True):
    """Verify KBC's file/viewer response, without following login redirects.

    Other providers retain their own browser embedding contract. No arbitrary
    client URL or API credential is sent through this check.
    """
    parsed = urlparse(url)
    if parsed.scheme == 'https' and parsed.netloc == 'view.officeapps.live.com':
        original = http_url((parse_qs(parsed.query).get('src') or [None])[0])
        if original and urlparse(original).netloc == 'kentbusinesscollege.org':
            # Office's shell loading says nothing about the underlying file.
            # Office fetches the file itself, so file X-Frame-Options don't apply.
            return check_source(original, framed=False)
    if parsed.scheme != 'https' or parsed.netloc not in {'kentbusinesscollege.org', 'kentbusinesscollege.sharepoint.com'}:
        return None
    cache_key = (hashlib.sha256(url.encode()).hexdigest(), framed)
    now = time.monotonic()
    with _cache_lock:
        cached = _preview_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]
    issue = None
    try:
        with open_source(url) as response:
            mime = response.headers.get('Content-Type', '').split(';')[0].lower()
            if not framed and mime == 'text/html':
                # Office needs file bytes, not a successful HTML/login shell.
                issue = 'source_content_missing'
            if framed and not mime.startswith(('audio/', 'video/', 'image/')) and response.headers.get('X-Frame-Options', '').upper() in ('DENY', 'SAMEORIGIN'):
                issue = 'source_embedding_denied'
            ancestors = re.search(r"(?:^|;)\s*frame-ancestors\s+([^;]+)", response.headers.get('Content-Security-Policy', ''), re.I)
            if framed and ancestors and ancestors[1].strip() in ("'self'", "'none'"):
                issue = 'source_embedding_denied'
            if '/text-body' in urlparse(response.geturl()).path:
                html = response.read(1024 * 1024).decode('utf-8', 'replace')
                body = re.sub(r'.*?<body[^>]*>', '', html, count=1, flags=re.I | re.S)
                body = re.sub(r'<h1\b[^>]*>.*?</h1>', '', body, flags=re.I | re.S)
                if not meaningful_html(body):
                    issue = 'source_content_missing'
            if urlparse(response.geturl()).path.endswith('/audio-player'):
                body = response.read(1024 * 1024).decode('utf-8', 'replace')
                media = re.search(r'<(?:audio|source)\b[^>]*src=["\']([^"\']+)', body, re.I)
                original = http_url(urljoin(url, html_entities.unescape(media[1]))) if media else None
                if not original or urlparse(original).path.endswith('/audio-player'):
                    issue = 'source_content_missing'
                else:
                    issue = issue or check_source(original, framed=False)
    except (urllib.error.HTTPError, OSError, ValueError):
        issue = 'source_unavailable'
    with _cache_lock:
        if len(_preview_cache) >= 10000:
            _preview_cache.clear()
        _preview_cache[cache_key] = (now + (15 if issue else 60), issue)
    return issue


def open_source(url):
    """Allow file redirects within KBC, never login pages or a different host.

    This carries no API key. Some real file endpoints implement GET but not
    HEAD; retry that method only when the server explicitly says so.
    """
    opener = urllib.request.build_opener(NoRedirect())
    origin = urlparse(url).netloc
    if origin not in {'kentbusinesscollege.org', 'kentbusinesscollege.sharepoint.com'}:
        raise ValueError('Unsupported material origin')
    for _ in range(4):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.netloc != origin or parsed.path in ('', '/') or parsed.path.startswith(('/wp-admin', '/wp-login')):
            raise ValueError('The source is not a material response')
        method = 'GET' if parsed.path.endswith(('/text-body', '/audio-player')) else 'HEAD'
        for attempt in range(2):
            try:
                return opener.open(urllib.request.Request(url, method=method,
                    headers={'User-Agent': 'KBC-Record-Review/1.0'}), timeout=6)
            except urllib.error.HTTPError as error:
                if error.code in (405, 501) and method == 'HEAD' and attempt == 0:
                    method = 'GET'
                    continue
                if error.code in (301, 302, 303, 307, 308) and error.headers.get('Location'):
                    url = urljoin(url, error.headers['Location'])
                    error.close()
                    break
                raise
    raise ValueError('Too many material redirects')


def validate_parts(parts):
    urls = sorted({part['url'] for part in parts if part['url']})
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = dict(zip(urls, pool.map(check_source, urls)))
    for part in parts:
        issue = checks.get(part['url']) if part['url'] else None
        if issue:
            for alternative in part.get('alternate_urls', []):
                if not check_source(alternative):
                    part['url'] = alternative
                    issue = None
                    break
            if issue:
                if meaningful_html(part.get('html')):
                    part['url'], issue = None, None
            if issue and part.get('_backup') and backup_available(part['_backup']):
                part['url'], issue = None, None
            if issue:
                material = live_material(part['id'])
                replacement, html = display_content({'material': material})
                if replacement and replacement != part['url'] and not check_source(replacement):
                    part['url'], issue = replacement, None
                elif html:
                    part['url'], part['html'], issue = None, html, None
            part.update(available=not issue, issue=issue)
        if not part.get('url') and not meaningful_html(part.get('html')) and part.get('_backup'):
            available = backup_available(part['_backup'])
            part.update(available=available, issue=None if available else 'source_unavailable')
        part.pop('_backup', None)
        part.pop('alternate_urls', None)
    return parts


def review(learner, rows):
    resolved = resolve(learner, rows, companions=True)
    validate_parts([part for item in resolved.values() for part in item['parts']])
    for item in resolved.values():
        if item['parts']:
            item['available'] = item['available'] and all(part['available'] for part in item['parts'])
    issues = [{'id': row['id'], 'title': row['title'], 'category': row['category'],
               'reason': 'Evidence document is missing.' if row['category'] == 'assignment'
               else 'The original learning material or saved quiz questions are unavailable.'}
              for row in rows if not resolved[row['id']]['available']]
    return {'ready': not issues, 'issues': issues}
