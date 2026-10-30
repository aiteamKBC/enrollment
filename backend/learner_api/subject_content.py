"""Legacy material recovery and server-side quiz definitions.

Source solutions never belong in a learner's open-quiz payload. Each new
attempt stores its own definition, so a later WordPress edit cannot change it.
"""

from copy import deepcopy
import html
import json
import math
import re
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit, parse_qs, unquote
import urllib.error
import urllib.request

from django.conf import settings


class ContentUnavailable(ValueError):
    pass


_cache = {}
_lock = Lock()


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target, origin = urlsplit(newurl), urlsplit(req.full_url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            raise ContentUnavailable('The material service redirected to another host.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def material_schema(activity_id):
    endpoint = getattr(settings, 'KBC_LMS_SCHEMA_URL', '').rsplit('/', 1)[0]
    secret = getattr(settings, 'KBC_LMS_API_KEY', '')
    if not endpoint or not secret:
        raise ContentUnavailable('The original material service is unavailable.')
    key = (endpoint, int(activity_id))
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > monotonic():
            return deepcopy(cached[1])
    request = urllib.request.Request(f'{endpoint}/material/{int(activity_id)}/schema', headers={
        'Accept': 'application/json', 'X-KBC-API-Key': secret, 'User-Agent': 'KBC-LearningOS/1.0',
    })
    try:
        with urllib.request.build_opener(_SameHostRedirect()).open(request, timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ContentUnavailable('The material definition is too large.')
        payload = json.loads(raw)
        if not isinstance(payload, dict) or int(payload.get('material_id', -1)) != int(activity_id):
            raise ContentUnavailable('The original material could not be verified.')
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        raise ContentUnavailable('The original material could not be loaded. Try again.') from error
    with _lock:
        if len(_cache) >= 256:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (monotonic() + 300, deepcopy(payload))
    return payload


def as_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def clean_text(value):
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', '', str(value or ''))).split())


def safe_url(value):
    value = html.unescape(str(value or '').strip())
    # The API credential is never a browser credential, including in source URLs.
    secret = getattr(settings, 'KBC_LMS_API_KEY', '')
    if secret and secret in value:
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    if parsed.scheme in ('https', 'http') and parsed.netloc and not parsed.username:
        return value
    if value.startswith(('/curriculum_api/curriculum/uploads/', '/learner_api/media/')) and not value.startswith('//'):
        return value
    return ''


def quiz_definition(quiz):
    if not isinstance(quiz, dict):
        return None
    raw_solutions = {str(row.get('question_id')): as_list(row.get('correct_answer'))
                     for row in as_list(quiz.get('solutions')) if isinstance(row, dict)}
    questions, issues = [], []
    seen = set()
    for index, raw in enumerate(as_list(quiz.get('questions'))):
        if not isinstance(raw, dict):
            issues.append('An original question is incomplete.')
            continue
        qid = str(raw.get('question_id') or index + 1)
        if qid in seen:
            issues.append('An original question has a duplicate identifier.')
        seen.add(qid)
        options = [{'id': str(option.get('option_order') or oi + 1),
                    'text': str(option.get('option_body') or option.get('option_text') or '')}
                   for oi, option in enumerate(as_list(raw.get('options'))) if isinstance(option, dict)]
        lookup = {}
        for option in options:
            lookup.setdefault(clean_text(option['text']), []).append(option['id'])
        solution_ids = []
        for answer in raw_solutions.get(qid, []):
            matches = lookup.get(clean_text(answer), [])
            if len(matches) == 1:
                solution_ids.append(matches[0])
            else:
                issues.append('An original answer cannot be matched to its options.')
        kind = raw.get('question_type') or 'single_choice'
        if kind not in ('single_choice', 'multi_choice', 'true_or_false', 'true_false'):
            issues.append('This question needs additional grading information from the original course.')
        if not solution_ids or not options:
            issues.append('The original quiz is missing answer definitions.')
        if len({o['id'] for o in options}) != len(options):
            issues.append('An original question has duplicate option identifiers.')
        if kind != 'multi_choice' and len(solution_ids) != 1:
            issues.append('The original single-choice answer is ambiguous.')
        questions.append({'id': qid, 'text': str(raw.get('question_body') or ''),
                          'type': kind, 'options': options, 'solution_ids': solution_ids})
    try:
        maximum = float(quiz.get('maximum_score'))
        passing = float(quiz.get('passing_score'))
        if not math.isfinite(maximum) or not math.isfinite(passing) or maximum <= 0 or not 0 <= passing <= maximum:
            raise ValueError()
        passing_percent = passing / maximum * 100
    except (ValueError, TypeError):
        passing_percent = None
        issues.append('The original passing score is unavailable.')
    if not questions:
        issues.append('The original quiz questions are unavailable.')
    configured = quiz.get('configured_questions')
    if configured is not None and str(configured).isdigit() and int(configured) != len(questions):
        issues.append('The original question list is incomplete.')
    return {'id': str(quiz.get('quiz_id') or ''), 'body': str(quiz.get('quiz_body') or ''),
            'questions': questions, 'passing_percent': passing_percent,
            'ready': not issues, 'message': next(iter(dict.fromkeys(issues)), ''),
            'scoring': 'equal_questions_all_correct_options'}


def public_quiz(quiz):
    if not quiz:
        return None
    return {key: ([{k: v for k, v in question.items() if k != 'solution_ids'} for question in value]
                  if key == 'questions' else value)
            for key, value in quiz.items() if key != 'scoring'}


def grade_quiz(definition, answers):
    quiz = definition.get('quiz')
    if not quiz or not quiz.get('ready'):
        raise ContentUnavailable('This quiz is not ready for automatic marking.')
    if not isinstance(answers, dict) or set(answers) != {q['id'] for q in quiz['questions']}:
        raise ValueError('Answer every question before submitting.')
    correct = 0
    review = []
    for question in quiz['questions']:
        selected = answers[question['id']]
        valid_ids = {option['id'] for option in question['options']}
        if not isinstance(selected, list) or not selected or any(not isinstance(v, str) or v not in valid_ids for v in selected):
            raise ValueError('One or more answers are invalid.')
        if len(set(selected)) != len(selected) or (question['type'] != 'multi_choice' and len(selected) != 1):
            raise ValueError('Select a valid answer for each question.')
        is_correct = set(selected) == set(question['solution_ids'])
        correct += is_correct
        review.append({'question_id': question['id'], 'is_correct': is_correct})
    score = correct / len(quiz['questions']) * 100
    return {'score_percent': round(score, 4), 'passed': score >= quiz['passing_percent'], 'review': review}


def _attachment_id(url):
    for _ in range(3):
        query = parse_qs(urlsplit(url).query)
        reference = (query.get('attachment_id') or [''])[0]
        if reference.isdigit():
            return reference
        url = (query.get('src') or [''])[0]
        if not url:
            break
    return ''


def _audio_media_kind(url):
    """A provider's HTML player cannot be used as an HTML5 audio source."""
    parsed = urlsplit(url)
    if (re.search(r'\.(?:mp3|m4a|aac|wav|ogg|oga|opus|flac|mp4|webm)$', parsed.path, re.I)
            or parsed.hostname == 'drive.google.com'
            or parsed.path.startswith('/learner_api/media/')):
        return 'audio'
    return 'embed'


def original_is_pdf(url):
    """Bounded MIME probe when the legacy schema API cannot identify a file.

    Only the configured source's attachment endpoint is contacted; redirects
    cannot escape that origin and no file bytes or signed URLs are logged.
    """
    target = urlsplit(url)
    origin = urlsplit(getattr(settings, 'KBC_LMS_SCHEMA_URL', ''))
    if (target.scheme != 'https' or (target.scheme, target.netloc) != (origin.scheme, origin.netloc)
            or target.username or not re.fullmatch(r'/wp-json/kbc-lms/v1/material/\d+/view', target.path)
            or not _attachment_id(url)):
        return False
    key = ('pdf-type', url)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
    try:
        request = urllib.request.Request(url, headers={'Range': 'bytes=0-4', 'User-Agent': 'KBC-LearningOS/1.0'})
        with urllib.request.build_opener(_SameHostRedirect()).open(request, timeout=10) as response:
            is_pdf = (response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() == 'application/pdf'
                      and response.read(5) == b'%PDF-')
    except (OSError, ContentUnavailable):
        return False
    with _lock:
        if len(_cache) >= 256:
            _cache.clear()
        _cache[key] = (monotonic() + 300, is_pdf)
    return is_pdf


def build_material(stored, schema=None, attachment_resolver=None, pdf_checker=None):
    row = stored['_source']
    media = []
    for key, kind in (('video_url', 'video'), ('audio_url', 'audio'), ('reading_url', 'document')):
        url = safe_url(stored.get(key))
        if url:
            media.append({'kind': _audio_media_kind(url) if kind == 'audio' else kind,
                          'url': url, 'title': stored['title']})
    reading = str(stored.get('reading_html') or '')
    remote_quiz = schema.get('quiz') if schema is not None else {
        'quiz_id': row.get('quiz_id'), 'quiz_body': row.get('quiz_body'),
        'questions': as_list(row.get('quiz_questions')),
        'passing_score': row.get('quiz_passing_score'), 'maximum_score': row.get('quiz_maximum_score'),
    } if row.get('quiz_id') or as_list(row.get('quiz_questions')) else None
    quiz = quiz_definition(remote_quiz)
    if schema:
        url = safe_url(schema.get('iframe_url'))
        kind = str(schema.get('content_type') or '').lower()
        component_type = str(schema.get('component_type') or '').lower()
        source = schema.get('source') if isinstance(schema.get('source'), dict) else {}
        if component_type != 'quiz' and url:
            media = [{'kind': 'video' if kind == 'video' else _audio_media_kind(url) if kind in ('audio', 'podcast') else 'document',
                      'url': url, 'title': stored['title'], 'can_embed': source.get('can_embed') is not False}]
        for field in ('reading_text_body', 'text_body', 'html_body'):
            if isinstance(schema.get(field), str) and schema[field]:
                reading = schema[field]
                break
    missing_attachments = []
    attachment_source = (schema or {}).get('source')
    attachments = as_list(attachment_source.get('attachments')) if isinstance(attachment_source, dict) else []
    if (schema or {}).get('lms_media_attachments') is not None:
        attachments = as_list(schema['lms_media_attachments'])
    local_attachments = {}
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        reference = str(attachment.get('attachment_id') or '')
        local = safe_url(attachment.get('lms_url'))
        # A migrated companion belongs to this exact attachment, on our origin.
        # Never accept an arbitrary origin or a different attachment's path.
        path = unquote(urlsplit(local).path)
        prefix = f'/curriculum_api/curriculum/uploads/_legacy_files/{reference}/'
        filename = path[len(prefix):] if path.startswith(prefix) else ''
        if (reference.isdigit() and local.startswith(prefix) and filename not in {'', '.', '..'}
                and not re.search(r'[/\\\x00-\x1f]', filename)):
            local_attachments[reference] = local
    if attachment_resolver or local_attachments:
        resolved = {}
        def archived(reference):
            if reference not in resolved:
                resolved[reference] = local_attachments.get(reference) or (
                    safe_url(attachment_resolver(reference)) if reference and attachment_resolver else '')
            return resolved[reference]
        # Prefer already transferred files; the existing attachment player can
        # recover empty legacy blobs through the original WordPress source.
        for item in media:
            reference = _attachment_id(item['url'])
            recovered = archived(reference)
            if recovered:
                item.update(url=recovered, can_embed=True)
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                continue
            reference = str(attachment.get('attachment_id') or '')
            recovered = archived(reference) if reference.isdigit() else ''
            title = str(attachment.get('filename') or attachment.get('file_title') or 'Attached file')
            if recovered:
                content_type = attachment.get('content_type')
                item = {'kind': content_type if content_type in ('audio', 'video') else 'document', 'url': recovered, 'title': title, 'can_embed': True}
                if index == 0 and not quiz:
                    media = [item, *media[1:]]
                elif all(value['url'] != recovered for value in media):
                    media.append(item)
            elif index > 0:
                missing_attachments.append(title)
    # Keep a lesson's reading companion alongside its quiz, even when WP's
    # navigation target points to the standalone quiz page.
    by_id = {str(a.get('attachment_id')): a for a in attachments if isinstance(a, dict)}
    source_origin = urlsplit(getattr(settings, 'KBC_LMS_SCHEMA_URL', ''))
    for item in media:
        viewer = urlsplit(item['url'])
        if viewer.hostname != 'view.officeapps.live.com':
            continue
        original = safe_url((parse_qs(viewer.query).get('src') or [''])[0])
        reference = _attachment_id(original)
        attachment = by_id.get(reference, {})
        mime_type = str(attachment.get('mime_type') or '').lower()
        is_pdf = mime_type == 'application/pdf' if mime_type else str((schema or {}).get('content_type') or '').lower() == 'pdf'
        target = urlsplit(original)
        trusted_attachment = (reference and target.scheme == 'https'
                              and (target.scheme, target.netloc) == (source_origin.scheme, source_origin.netloc)
                              and re.fullmatch(r'/wp-json/kbc-lms/v1/material/\d+/view', target.path))
        if trusted_attachment and not mime_type and not is_pdf and pdf_checker:
            is_pdf = pdf_checker(original)
        if is_pdf and trusted_attachment:
            # Office Online cannot render these PDFs. The learner API serves
            # their verified original bytes to the existing local PDF renderer.
            item.update(kind='pdf', url=original, attachment_id=reference,
                        file_name=attachment.get('filename') or 'document.pdf', can_embed=True)
    has_reading = bool(reading or any(item['kind'] == 'document' for item in media))
    has_reading = has_reading or any(item['kind'] == 'pdf' for item in media)
    return {'title': stored['title'], 'reading_html': reading, 'media': media,
            'quiz': quiz, 'has_reading': has_reading,
            'available': bool(reading or media or quiz), 'source_live': schema is not None,
            'unavailable_attachments': missing_attachments}
