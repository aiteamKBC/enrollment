"""Conditional, pre-encoded JSON responses for cached curriculum reads.

The curriculum read endpoints already cache the *payload* they build (see
``views.cached_curriculum_value``). What they still did on every request was:

* serialise that payload to JSON again -- ~0.2s for the 34 MB programme
  coverage document, on every hit;
* let ``GZipMiddleware`` compress the result again -- longer still;
* let ``ConditionalGetMiddleware`` hash the whole body to decide whether a 304
  was possible, which it can only do *after* the two steps above.

This module caches the encoded response instead: the JSON bytes, their gzip
form and a content ETag, computed once when the payload is built. A hit then
costs a dict lookup, and a client that already holds the current body gets a
304 without the server touching the payload at all.

Correctness rests on one rule: **the ETag is a hash of the exact body this
server would send right now.** A 304 therefore never tells a client anything a
200 would not have told it; it only spares the transfer. It cannot make a
response staler than the underlying cache already is, so each endpoint's
freshness is still decided where it always was -- the cache key, the shared
epoch that curriculum writes bump, and the TTL the endpoint chooses. In
particular it adds nothing for data that changes outside curriculum writes
(enrolment placements, learner progress): those endpoints keep their short
TTLs, and the ETag simply follows the body when it changes.

Authorisation is untouched: ``login.api_gate.ApiSessionGateMiddleware`` and the
view decorators run before any of this, and no endpoint routed here varies its
body by user, so an ETag can only ever be matched by a caller who was entitled
to the identical 200.
"""

import gzip
import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, HttpResponseNotModified

# Below this a compressed body is not worth the bytes of the gzip header, and
# GZipMiddleware itself declines anything shorter than 200 bytes.
GZIP_MIN_BYTES = 200
# Matches django.utils.text.compress_string, so a pre-compressed body is the
# same size GZipMiddleware would have produced.
GZIP_LEVEL = 6


def encode_json_entry(payload, status=200):
    """The cacheable, encoded form of ``payload``.

    Serialised exactly as ``JsonResponse`` serialises (``DjangoJSONEncoder``,
    default separators), so a client sees byte-for-byte the body it received
    before this module existed.

    Only the compressed body is kept when compression pays: it is what nearly
    every browser asks for, it is a fraction of the size to hold in the process
    cache and to ship through Redis, and the rare client that does not accept
    gzip is served by decompressing it.
    """
    return encode_json_bytes(json.dumps(payload, cls=DjangoJSONEncoder).encode('utf-8'), status)


def encode_json_bytes(body, status=200):
    """The encoded entry for JSON that is already serialised.

    For a builder that produces a ``JsonResponse`` (with its status) rather than
    a payload: its ``content`` is taken as the body as-is, so nothing is decoded
    and re-serialised -- for the 34 MB coverage document that round trip was
    most of a cache fill.
    """
    body = bytes(body)
    etag = 'W/"c%s"' % hashlib.sha256(body).hexdigest()[:40]
    if len(body) >= GZIP_MIN_BYTES:
        return {
            'status': int(status),
            'etag': etag,
            'gzip': gzip.compress(body, compresslevel=GZIP_LEVEL, mtime=0),
            'length': len(body),
        }
    return {'status': int(status), 'etag': etag, 'body': body, 'length': len(body)}


def encode_json_error_entry(message, status=400, **extra):
    """An encoded entry carrying exactly the body ``views.json_error`` returns."""
    return encode_json_entry({'error': message, **extra}, status=status)


def _snake_case(name):
    return ''.join(f'_{char.lower()}' if char.isupper() else char for char in name)


def query_value(request, name):
    """A query parameter read under its camelCase name or its snake_case alias.

    The curriculum read API grew both spellings (``deferStats``/``defer_stats``,
    ``sourceType``/``source_type``) one endpoint at a time. New parameters are
    documented in camelCase, which is what the frontend client sends; the
    snake_case alias keeps every existing caller and bookmark working. The
    camelCase spelling wins when a request carries both.
    """
    value = request.GET.get(name)
    if value in (None, ''):
        value = request.GET.get(_snake_case(name))
    return '' if value is None else str(value).strip()


def query_flag(request, name):
    """A boolean query parameter, with views.truthy()'s accepted spellings."""
    return query_value(request, name).lower() in {'1', 'true', 'yes', 'y', 'on'}


def query_list(request, name):
    """A comma-separated query parameter as a set of lower-cased names."""
    return {part.strip().lower() for part in query_value(request, name).split(',') if part.strip()}


def entry_body(entry):
    """The uncompressed JSON bytes of an encoded entry."""
    if 'body' in entry:
        return entry['body']
    return gzip.decompress(entry['gzip'])


def entry_payload(entry):
    """The decoded payload of an encoded entry, for callers that post-process it."""
    return json.loads(entry_body(entry))


def _etag_opaque(tag):
    tag = tag.strip()
    if tag.startswith('W/'):
        tag = tag[2:]
    return tag


def if_none_match_hits(request, etag):
    """Weak comparison (RFC 9110 13.1.2), the form If-None-Match uses."""
    header = request.headers.get('If-None-Match') or ''
    if not header:
        return False
    if header.strip() == '*':
        return True
    wanted = _etag_opaque(etag)
    return any(_etag_opaque(candidate) == wanted for candidate in header.split(','))


def accepts_gzip(request):
    """True when the client takes gzip: listed (or ``*``) with a non-zero q."""
    accept = (request.headers.get('Accept-Encoding') or '').lower()
    for part in accept.split(','):
        name, _, params = part.strip().partition(';')
        if name.strip() not in {'gzip', '*'}:
            continue
        quality = 1.0
        for param in params.split(';'):
            key, _, value = param.strip().partition('=')
            if key.strip() == 'q':
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def conditional_json_response(request, entry, *, cache_status=''):
    """Serve an encoded entry: a 304 when the client already holds it.

    ``Cache-Control: private, no-cache`` keeps the body out of shared caches
    and makes the browser revalidate on every use, which is what lets the ETag
    do the work: there is no client-side TTL that could outlive a server-side
    change. ``Vary: Accept-Encoding`` because the stored representation
    depends on it. The ETag is weak since one tag covers both encodings.
    """
    status = entry.get('status', 200)
    if status == 200 and if_none_match_hits(request, entry['etag']):
        response = HttpResponseNotModified()
    elif 'gzip' in entry and accepts_gzip(request):
        response = HttpResponse(entry['gzip'], content_type='application/json', status=status)
        response['Content-Encoding'] = 'gzip'
    else:
        response = HttpResponse(entry_body(entry), content_type='application/json', status=status)
    if status == 200:
        response['ETag'] = entry['etag']
    response['Cache-Control'] = 'private, no-cache'
    response['Vary'] = 'Accept-Encoding, Cookie'
    if cache_status:
        response['X-LMS-Cache'] = cache_status
    return response


def plain_json_response(entry):
    """The entry as an ordinary, uncompressed response.

    For in-process callers that read ``response.content`` themselves (the KSB
    trace and readiness views parse the coverage document), which must never
    be handed gzip bytes whatever the browser's Accept-Encoding said.
    """
    return HttpResponse(entry_body(entry), content_type='application/json', status=entry.get('status', 200))
