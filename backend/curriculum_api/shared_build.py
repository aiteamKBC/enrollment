"""One rebuild per cache key across every worker, when the cache is shared.

``views.cached_curriculum_value`` already serialises rebuilds of one key inside
a process (a per-key ``threading.Lock``). Production runs several services of
several workers each, and on a miss every one of them used to run the same
multi-second build against the same database at the same moment -- a programme
page opened by three people on three workers was three identical 30s reads.

With a Redis-backed cache the workers share the result anyway, so they can
share the work too: the first worker to miss takes a short-lived lock with
``cache.add`` (atomic in Redis) and builds; the others wait for the value to
appear under the same epoch-versioned key and serve it.

Every failure mode falls back to building locally, which is exactly the old
behaviour:

* a per-process cache (LocMemCache, or ``ResilientRedisCache`` while it is
  degraded) gives no cross-worker guarantee, so no lock is taken at all;
* if the lock cannot be read or written, the caller builds;
* if the holder fails, or its result is not published (a write moved the epoch
  mid-build, so the result is discarded on purpose), the lock is released and
  a waiter takes it over;
* a waiter never waits longer than ``WAIT_SECONDS``; then it builds itself.

Nothing here decides freshness. The value a waiter receives is the one the
builder published under the same epoch-versioned key, by the same rules a
cache hit is already served by.
"""

import logging
import time
import uuid

logger = logging.getLogger(__name__)

# Long enough to cover the slowest build seen (~90s against the remote
# database), so a live build is never mistaken for an abandoned one; a crashed
# holder's lock simply expires.
LOCK_SECONDS = 180
# A waiter gives up and builds by itself after this long.
WAIT_SECONDS = 120
POLL_SECONDS = 0.25


def cache_is_shared(cache):
    """True when ``cache`` is a store every worker sees (Redis), right now."""
    name = f'{type(cache).__module__}.{type(cache).__name__}'.lower()
    if 'redis' not in name:
        return False
    # ResilientRedisCache serves from a per-process fallback while Redis is
    # unreachable; a lock taken there would be this process's alone.
    degraded_until = getattr(cache, '_degraded_until', 0.0) or 0.0
    return degraded_until <= time.monotonic()


def lock_key_for(shared_key):
    return f'{shared_key}:build-lock'


def try_acquire(cache, shared_key):
    """``(acquired, token)``. ``acquired`` is True when this caller should build.

    A cache that is not shared, or that fails, answers "build" with no token:
    the caller proceeds exactly as it did before this module existed.
    """
    if not cache_is_shared(cache):
        return True, None
    token = uuid.uuid4().hex
    try:
        if cache.add(lock_key_for(shared_key), token, timeout=LOCK_SECONDS):
            return True, token
        return False, None
    except Exception:
        logger.warning('Unable to take the curriculum build lock; building locally.', exc_info=True)
        return True, None


def release(cache, shared_key, token):
    """Drop the lock if this caller still holds it. Best effort."""
    if not token:
        return
    key = lock_key_for(shared_key)
    try:
        # Not atomic, but the only way to lose is to delete a lock whose own
        # holder has outlived LOCK_SECONDS -- and the cost of that is one extra
        # build, never a wrong value.
        if cache.get(key) == token:
            cache.delete(key)
    except Exception:
        logger.warning('Unable to release the curriculum build lock.', exc_info=True)


def wait_for_value(cache, shared_key, *, wait_seconds=WAIT_SECONDS, poll_seconds=POLL_SECONDS, sleep=time.sleep):
    """``(value, holder_gone)`` once another worker's build lands or stops.

    ``value`` is the published value, or None. ``holder_gone`` is True when the
    lock disappeared without a value -- the build failed or was discarded --
    so the caller may try to take the lock itself.
    """
    deadline = time.monotonic() + wait_seconds
    lock_key = lock_key_for(shared_key)
    while time.monotonic() < deadline:
        sleep(poll_seconds)
        try:
            value = cache.get(shared_key)
            if value is not None:
                return value, False
            if cache.get(lock_key) is None:
                # One last look: the holder publishes before it releases, but
                # the two reads above are not atomic.
                return cache.get(shared_key), True
        except Exception:
            logger.warning('Unable to read the shared curriculum cache while waiting.', exc_info=True)
            return None, True
    return None, False
