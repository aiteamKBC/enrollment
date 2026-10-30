"""Read missing graded attempts from the original LMS, without importing data.

The upstream supports pagination only: email/student_id filters are ignored.
Page hints contain routing metadata, never answers, and are not authorization.
Every returned answer is selected by exact learner, group and activity IDs.
"""
import gzip
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from django.conf import settings

from .content import NoRedirect, ORIGIN, obj, values

_cache = {}
_lock = threading.RLock()
_page_locks = {}
_hints = None
PAGE_SIZE = 20
MAX_BYTES = 80 * 1024 * 1024


def hints():
    global _hints
    if not _hints:
        try:
            _hints = json.loads(Path(__file__).with_name('source_page_hints.json').read_text(encoding='utf-8'))['pages']
        except (OSError, ValueError, KeyError):
            _hints = {}
    return _hints


def fetch_page(page):
    """Keep only linked learners in a short private process cache."""
    key = getattr(settings, 'KBC_LMS_API_KEY', '')
    if not key:
        return None
    cache_key = (hashlib.sha256(key.encode()).hexdigest(), page)
    with _lock:
        gate = _page_locks.setdefault(cache_key, threading.Lock())
    with gate:
        now = time.monotonic()
        with _lock:
            cached = _cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]
        request = urllib.request.Request(f'{ORIGIN}/wp-json/kbc-lms/v1/all-students-schema?page={page}&per_page={PAGE_SIZE}',
            headers={'X-KBC-API-Key': key, 'Accept': 'application/json', 'Accept-Encoding': 'gzip',
                     'User-Agent': 'KBC-Record-Review/1.0'})
        result = None
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
                raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('Source page exceeds the response bound')
                payload = obj(json.loads(raw))
            pagination = obj(payload.get('pagination'))
            if pagination.get('page') != page or pagination.get('per_page') != PAGE_SIZE:
                raise ValueError('Source did not honour pagination')
            allowed = hints()
            learners, ids = {}, set()
            for group in values(payload.get('groups')):
                for learner in values(group.get('learners')):
                    ident = learner.get('learner_id')
                    if not isinstance(ident, int):
                        continue
                    ids.add(ident)
                    if str(ident) not in allowed:
                        continue
                    records = learners.setdefault(ident, {})
                    for activity in values(learner.get('activity_results')):
                        quiz = obj(activity.get('quiz_result'))
                        if values(quiz.get('answers')):
                            records[(group.get('group_id'), activity.get('activity_id'))] = {
                                'group_id': group.get('group_id'), 'activity_id': activity.get('activity_id'),
                                'quiz_attempted': quiz.get('attempted'), 'quiz_passed': quiz.get('passed'),
                                'quiz_score': quiz.get('score'), 'quiz_maximum_score': quiz.get('maximum_score'),
                                'quiz_attempt_number': quiz.get('attempt_number'), 'quiz_answers': quiz.get('answers')}
            result = {'learners': learners, 'ids': ids, 'pages': pagination.get('total_pages') or page}
        except (OSError, ValueError, EOFError, TypeError, AttributeError):
            pass
        with _lock:
            # Keep a bounded amount of private data; no disk/DB record cache.
            if len(_cache) >= 60:
                _cache.clear()
            _cache[cache_key] = (time.monotonic() + (30 if result else 5), result)
        return result


def read(learner, identities):
    ident = learner.get('lms_id')
    page = hints().get(str(ident))
    if not identities or not isinstance(ident, int) or not page:
        return {}
    # A hint may move when the source roster changes. Check neighbouring pages;
    # never accept the first returned student as the requested learner.
    for candidate in dict.fromkeys((page, max(1, page - 1), page + 1)):
        payload = fetch_page(candidate)
        if payload is None:
            return {}
        if ident in payload['ids']:
            records = payload['learners'].get(ident, {})
            return {pair: records[pair] for pair in identities if pair in records}
    return {}
