"""Bounded, process-local row reuse, verified against SQL on every read.

An unrelated curriculum write invalidates response caches. It need not transfer
megabytes of unchanged authoring content again. Read current row fingerprints,
then fetch only changed/new rows. This is not a TTL-based source of truth: the
database still decides membership, order and every selected value on each call.
"""
from collections import OrderedDict
import hashlib
import json
import pickle
import threading
import time
import zlib


_FINGERPRINT = '__authoring_row_sha256'
_MAX_BYTES = 32 * 1024 * 1024
_MAX_ENTRY_BYTES = 8 * 1024 * 1024
_MAX_ENTRIES = 64
_TTL_SECONDS = 15 * 60
_SNAPSHOTS = OrderedDict()
_LOCK = threading.Lock()


def _key(connection, table, where, params, order, columns):
    config = getattr(connection, 'settings_dict', None)
    namespace = (
        {name: config.get(name) for name in ('ENGINE', 'HOST', 'PORT', 'NAME', 'USER', 'OPTIONS')}
        if isinstance(config, dict) else {'connection': id(connection)}
    )
    return hashlib.sha256(json.dumps(
        [namespace, table, where, params, order, columns], sort_keys=True, default=str,
    ).encode()).hexdigest()


def _load(key):
    with _LOCK:
        item = _SNAPSHOTS.get(key)
        if item is None:
            return None
        expiry, compressed = item
        if expiry <= time.monotonic():
            del _SNAPSHOTS[key]
            return None
        _SNAPSHOTS.move_to_end(key)
    # Only bytes serialized by _store in this process reach pickle.loads. No
    # files, shared cache, requests or database values can supply this envelope.
    # Each load also gives callers independent dicts/lists, never cached objects.
    return pickle.loads(zlib.decompress(compressed))


def _store(key, rows):
    try:
        compressed = zlib.compress(pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL), level=1)
    except (TypeError, pickle.PicklingError):
        # A future native database type may not be serializable. The fresh
        # database result is still valid; simply don't retain this snapshot.
        return
    if len(compressed) > _MAX_ENTRY_BYTES:
        return
    with _LOCK:
        now = time.monotonic()
        for expired in [k for k, (expiry, _) in _SNAPSHOTS.items() if expiry <= now]:
            del _SNAPSHOTS[expired]
        _SNAPSHOTS[key] = (now + _TTL_SECONDS, compressed)
        _SNAPSHOTS.move_to_end(key)
        while len(_SNAPSHOTS) > _MAX_ENTRIES or sum(len(item[1]) for item in _SNAPSHOTS.values()) > _MAX_BYTES:
            _SNAPSHOTS.popitem(last=False)


def _fingerprint_sql(columns):
    from . import views

    # Text preserves even JSON whitespace, decimal scale and native SQL types:
    # the existing structureRevision fingerprints these original decoded rows.
    # Including types prevents a same-text schema conversion reusing old types.
    fields = ', '.join(
        f'row(pg_typeof({views.quote_ident(c)})::text, {views.quote_ident(c)}::text)'
        for c in columns
    )
    return f"encode(sha256(convert_to(to_jsonb(row({fields}))::text, 'UTF8')), 'hex')"


def read_verified_rows(table, where, params, order, columns):
    from . import views
    from .authoring_read_transport import _compact_rows

    key = _key(views.connection, table, where, params, order, columns)
    cached = _load(key)
    fingerprint = _fingerprint_sql(columns)
    fingerprint_field = f'{fingerprint} as {views.quote_ident(_FINGERPRINT)}'

    def fetch(scope=where, arguments=params):
        return _compact_rows(table, scope, arguments, order, columns, fingerprint_sql=fingerprint_field)

    if cached is None:
        rows = fetch()
    else:
        # Usually no row changed. Compare one ordered set digest first, rather
        # than transferring thousands of (id, digest) pairs on every rebuild.
        # Row digests are fixed-width hex, so their concatenation is unambiguous.
        expected = hashlib.sha256(''.join(row[_FINGERPRINT] for row in cached).encode()).hexdigest()
        with views.connection.cursor() as cursor:
            cursor.execute(
                "select encode(sha256(convert_to(coalesce("
                f"string_agg({fingerprint}, '' order by {order}), ''), 'UTF8')), 'hex') "
                f'from {views.authoring_table_name(table)} where {where}', params,
            )
            current_digest = cursor.fetchone()[0]
        if current_digest == expected:
            return [{k: v for k, v in row.items() if k != _FINGERPRINT} for row in cached]
        with views.connection.cursor() as cursor:
            cursor.execute(
                f'select id, {fingerprint_field} from {views.authoring_table_name(table)} '
                f'where {where} order by {order}', params,
            )
            current = views.rows_as_dicts(cursor)
        by_id = {row['id']: row for row in cached}
        missing = [row['id'] for row in current
                   if by_id.get(row['id'], {}).get(_FINGERPRINT) != row[_FINGERPRINT]]
        if len(missing) > max(1000, len(current) // 2):
            # A wholesale rewrite is cheaper as one fresh snapshot than many
            # batches, and does not need any values from the cached snapshot.
            rows = fetch()
            _store(key, rows)
            return [{k: v for k, v in row.items() if k != _FINGERPRINT} for row in rows]
        if missing:
            fetched = []
            # Bound parameter count for a programme containing thousands of rows.
            for offset in range(0, len(missing), 1000):
                ids = missing[offset:offset + 1000]
                fetched.extend(fetch(
                    f'({where}) and id in ({", ".join(["%s"] * len(ids))})', [*params, *ids],
                ))
            by_id.update((row['id'], row) for row in fetched)
        # A writer may commit between the fingerprint and content SELECTs. Do
        # not combine different snapshots, or certify them with a revision.
        # A single fresh read is the safe fallback, never an unbounded retry.
        if any(by_id.get(row['id'], {}).get(_FINGERPRINT) != row[_FINGERPRINT] for row in current):
            rows = fetch()
        else:
            rows = [by_id[row['id']] for row in current]
    _store(key, rows)
    return [{k: v for k, v in row.items() if k != _FINGERPRINT} for row in rows]
