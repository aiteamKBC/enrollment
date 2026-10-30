"""Public booking cards, with directory management restricted to authorised staff."""
import json
from urllib.parse import urlsplit

from django.db import connections, DatabaseError, IntegrityError, transaction
from django.http import JsonResponse
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from .permissions import require_access

ONE_TO_ONE = 'one_to_one'
LINK_FIELDS = ('first_session', 'support_session', 'coaching_session', 'progress_review', ONE_TO_ONE)
TABLE = 'login.coach_directory'


def validated(payload):
    if not isinstance(payload, dict):
        raise ValueError('A coach object is required.')
    name = str(payload.get('name') or '').strip()
    if not name or len(name) > 255:
        raise ValueError('Enter a coach name of up to 255 characters.')
    links = payload.get('links', {})
    if not isinstance(links, dict):
        raise ValueError('Booking links must be an object.')
    clean = {}
    for key in LINK_FIELDS:
        value = links.get(key) or ''
        if not isinstance(value, str):
            raise ValueError('Booking links must be URLs.')
        value = value.strip()
        if value:
            parsed = urlsplit(value)
            if (len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value)
                    or '\\' in value or parsed.scheme not in ('https', 'http')
                    or not parsed.hostname or parsed.username or parsed.password):
                raise ValueError('Use a valid http or https booking URL without credentials.')
            parsed.port
        clean[key] = value
    return name, clean


def rows(cursor):
    result = []
    for row in cursor.fetchall():
        links = row[3] if isinstance(row[3], dict) else json.loads(row[3])
        # Rows saved before a booking type existed simply lack its key; report it
        # as blank so every client sees the same set of options.
        links = {**{key: '' for key in LINK_FIELDS}, **links}
        result.append({'id': row[0], 'name': row[1], 'slug': row[2], 'links': links, 'version': row[4]})
    return result


def _pages_named(name):
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(
            f'SELECT id,name,slug,links,version FROM {TABLE} WHERE lower(btrim(name))=lower(%s) OR slug=%s',
            [name, slugify(name)],
        )
        return rows(cursor)


def one_to_one_page(owner_name):
    """The directory page offering a one-to-one with this case owner, or None.

    ``Case_owner`` holds the staff member's name, and the directory is keyed by
    name too, so the match is on the name (or the slug it produced). Ambiguous
    matches return None rather than risk sending a learner to another coach, and
    a page without a one-to-one link is not offered at all.

    ``Case_owner`` is a full name ("Med Maher") while most pages carry only the
    first name ("Med"), so when no page has the full name the first name is
    tried instead -- still only accepted when exactly one page has it.
    """
    owner_name = str(owner_name or '').strip()
    if not owner_name:
        return None
    found = _pages_named(owner_name)
    first_name = owner_name.split()[0]
    if not found and first_name != owner_name:
        found = _pages_named(first_name)
    if len(found) != 1 or not found[0]['links'].get(ONE_TO_ONE):
        return None
    return {'name': found[0]['name'], 'slug': found[0]['slug']}


@require_GET
def public_coach(request, slug):
    try:
        with connections['enrolment'].cursor() as cursor:
            cursor.execute(f'SELECT id,name,slug,links,version FROM {TABLE} WHERE slug=%s', [slug])
            found = rows(cursor)
        if not found:
            return JsonResponse({'error': 'Coach page not found.'}, status=404)
        # No account identifiers, emails, internal staff records or admin metadata.
        return JsonResponse({key: found[0][key] for key in ('name', 'slug', 'links')})
    except DatabaseError:
        return JsonResponse({'error': 'Coach bookings are temporarily unavailable.'}, status=503)


@csrf_exempt
@require_access('enrolment', 'curriculum')
def directory(request, pk=None):
    if request.method not in ('GET', 'POST', 'PUT', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    if request.method != 'GET' and request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Missing X-Requested-With header.'}, status=403)
    try:
        with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cursor:
            if request.method == 'GET' and pk is None:
                cursor.execute(f'SELECT id,name,slug,links,version FROM {TABLE} ORDER BY lower(name),id')
                return JsonResponse({'coaches': rows(cursor)})
            payload = json.loads(request.body or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('A coach object is required.')
            if request.method == 'POST' and pk is None:
                name, links = validated(payload)
                slug = slugify(name)
                if not slug:
                    raise ValueError('The coach name must contain letters or numbers.')
                cursor.execute(f'INSERT INTO {TABLE} (name,slug,links) VALUES (%s,%s,%s::jsonb) RETURNING id,name,slug,links,version', [name, slug, json.dumps(links)])
                return JsonResponse(rows(cursor)[0], status=201)
            if request.method in ('PUT', 'DELETE') and pk is not None:
                version = payload.get('version')
                if type(version) is not int or version < 1:
                    raise ValueError('Reload the coach before saving changes.')
                if request.method == 'DELETE':
                    cursor.execute(f'DELETE FROM {TABLE} WHERE id=%s AND version=%s RETURNING id', [pk, version])
                    if not cursor.fetchone():
                        return JsonResponse({'error': 'Coach changed or was removed. Reload and retry.'}, status=409)
                    return JsonResponse({'deleted': True})
                name, links = validated(payload)
                cursor.execute(f'UPDATE {TABLE} SET name=%s,links=%s::jsonb,version=version+1,updated_at=now() WHERE id=%s AND version=%s RETURNING id,name,slug,links,version', [name, json.dumps(links), pk, version])
                found = rows(cursor)
                if not found:
                    return JsonResponse({'error': 'Coach changed or was removed. Reload and retry.'}, status=409)
                return JsonResponse(found[0])
            return JsonResponse({'error': 'Method not allowed.'}, status=405)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Enter a valid coach name and http/https booking links, then retry.'}, status=400)
    except IntegrityError:
        return JsonResponse({'error': 'A coach with this page address already exists.'}, status=409)
    except DatabaseError:
        return JsonResponse({'error': 'Coach directory is temporarily unavailable.'}, status=503)
