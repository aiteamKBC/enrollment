"""Start the durable session queue from serving processes, never on import.

The database worker owns leases, retries and occurrence binding. These threads
only wake it up; HTTP requests never transfer recording bytes themselves.
"""
import io
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

log = logging.getLogger(__name__)
POLL_SECONDS = 60
REVIEW_SECONDS = 5 * 60
MAX_MANUAL_WORKERS = 2
# A normal Django autoreload replaces the process. Preserve handles as well
# when tooling re-executes this module in the same process: losing a live
# thread reference would let the next request create a duplicate reviewer.
if '_lock' not in globals():
    _lock = threading.Lock()
    _automatic_thread = None
    _review_thread = None
    _manual_series = set()
    _stop = threading.Event()
if '_foreground' not in globals():
    _foreground = threading.Condition()
    _active_requests = 0
REVIEW_IDLE_GRACE_SECONDS = 30


def _log_command_summary(output, pattern, label):
    # Commands may print private transport details: only allow numeric summaries.
    for line in output.getvalue().splitlines():
        if re.fullmatch(pattern, line):
            log.info('%s: %s', label, line)


def run_worker(*, series_id=None, scheduled=False):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import close_old_connections, connections

    output = io.StringIO()
    try:
        close_old_connections()
        call_command('process_session_results', scheduled=scheduled,
                     limit=1 if series_id else 10,
                     live_session_ids=[series_id] if series_id else [],
                     stdout=output, stderr=io.StringIO())
    except CommandError as error:
        if getattr(error, 'session_jobs_need_retry', False):
            log.warning('Session sync saved retry state for %d jobs; saved results remain available.',
                        min(100, max(0, int(getattr(error, 'failed_jobs', 0)))))
        else:
            log.error('Session sync command failed (CommandError). Check saved job status and server configuration.')
    except Exception as error:
        # The command persists job failures; never log tokens, URLs or rosters.
        log.error('Session sync worker failed (%s). Check saved job status and server configuration.',
                  type(error).__name__)
    finally:
        _log_command_summary(output, r'Processed [0-9]+ session jobs; [0-9]+ need retry\.', 'Session sync')
        connections.close_all()


def run_reviewer():
    from django.core.management import call_command
    from django.db import close_old_connections, connections

    output = io.StringIO()
    try:
        close_old_connections()
        call_command('review_session_recordings', stdout=output, stderr=io.StringIO())
    except Exception as error:
        log.error('Session recording review failed (%s); the next pass will retry.', type(error).__name__)
    finally:
        _log_command_summary(output,
                             r'Checked [0-9]+ saved recordings; [0-9]+ queued for repair; [0-9]+ need review\.',
                             'Session recording review')
        connections.close_all()


def _automatic_loop():
    while not _stop.is_set():
        try:
            if not _wait_for_foreground():
                return
            run_worker(scheduled=True)
        except Exception as error:
            log.error('Automatic session sync could not run (%s); it will retry.', type(error).__name__)
        _stop.wait(POLL_SECONDS)


@contextmanager
def _foreground_request():
    """Protect view handling; WSGI streaming iteration stays unchanged."""
    global _active_requests
    with _foreground:
        _active_requests += 1
    try:
        yield
    finally:
        with _foreground:
            _active_requests -= 1
            _foreground.notify_all()


def _wait_for_foreground():
    # Background waits for current views; views never wait for the reviewer.
    deadline = time.monotonic() + REVIEW_IDLE_GRACE_SECONDS
    with _foreground:
        while _active_requests:
            if _stop.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # Continuous traffic must not starve availability review.
            _foreground.wait(timeout=min(1, remaining))
    return True


def _claim_review_slot():
    """Coalesce periodic HEAD passes using the existing shared cache.

    Local-memory/fallback caches only coalesce within one process. Cache
    failure must not disable availability review; duplicate work is safer.
    Manual management commands deliberately do not use this claim.
    """
    from django.core.cache import cache
    slot = int(time.time() // REVIEW_SECONDS)
    try:
        return bool(cache.add(f'session-recording-review:slot:{slot}', 1,
                              timeout=REVIEW_SECONDS * 2))
    except Exception as error:
        log.warning('Recording review coordination unavailable (%s); reviewing locally.',
                    type(error).__name__)
        return True


def _review_loop():
    # The first HTTP request starts this thread. Do not HEAD every recent
    # recording during that request's cold start; retain the periodic review.
    if _stop.wait(REVIEW_SECONDS):
        return
    while not _stop.is_set():
        try:
            if not _wait_for_foreground():
                return
            if _claim_review_slot():
                run_reviewer()
        except Exception as error:
            log.error('Automatic session recording review could not run (%s); it will retry.', type(error).__name__)
        _stop.wait(REVIEW_SECONDS)


def start_automatic_sync():
    """Start separate sync and reviewer threads after the serving process forks.

    Invoked by the HTTP server wrappers after workers have forked. Django test
    clients and management commands do not enter these wrappers. The explicit
    opt-out supports deployments that retain their external scheduler instead.
    """
    global _automatic_thread, _review_thread
    if os.environ.get('SESSION_RESULTS_AUTOMATIC', 'true').lower() in {'0', 'false', 'off'}:
        return False
    if any(arg in {'test', 'pytest', 'py.test'} for arg in sys.argv) or 'PYTEST_CURRENT_TEST' in os.environ:
        return False
    from django.conf import settings
    if getattr(settings, 'CHAT_TEST_MODE', False) or getattr(settings, 'RUN_APP_ON_TEST_BRANCH', False):
        return False
    with _lock:
        for current, target, name in (
                (_automatic_thread, _automatic_loop, 'session-results-auto'),
                (_review_thread, _review_loop, 'session-recording-review')):
            if current is not None and current.is_alive():
                continue
            try:
                thread = threading.Thread(target=target, name=name, daemon=True)
                thread.start()
            except RuntimeError as error:
                log.error('Could not start %s (%s).', name, type(error).__name__)
                continue
            if target is _automatic_loop:
                _automatic_thread = thread
            else:
                _review_thread = thread
        return (_automatic_thread is not None and _automatic_thread.is_alive()
                and _review_thread is not None and _review_thread.is_alive())


def _manual_run(series_id):
    try:
        run_worker(series_id=series_id)
    finally:
        with _lock:
            _manual_series.discard(series_id)


def start_requested_sync(series_id):
    """Immediately attempt this series after its queue transaction commits.

    Repeated clicks are coalesced locally; the worker's database lease also
    protects against other web processes and the periodic scheduler. Overflow
    remains in the durable queue for the automatic worker.
    """
    with _lock:
        if series_id in _manual_series or len(_manual_series) >= MAX_MANUAL_WORKERS:
            return False
        _manual_series.add(series_id)
        try:
            threading.Thread(target=_manual_run, args=(series_id,),
                             name='session-results-manual', daemon=True).start()
        except RuntimeError:
            _manual_series.discard(series_id)
            raise
    return True


class SessionSyncWSGI:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        with _foreground_request():
            start_automatic_sync()
            return self.application(environ, start_response)


class SessionSyncASGI:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            with _foreground_request():
                start_automatic_sync()
                await self.application(scope, receive, send)
        else:
            await self.application(scope, receive, send)
