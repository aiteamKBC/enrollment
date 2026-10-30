"""A module's "Create Teams calendar" form, saved without creating anything.

The create dialog is filled in once and booked once. Closing it before Create
threw every answer away, so an author who had not finished -- waiting on a
co-organiser's address, say -- had to start again. A draft keeps the form on
the server against the module, so whoever opens Create next starts from it.
The additional week meeting form (``teams_week_meeting``) keeps its own draft
beside it, one per module, as ``kind=week``.

Nothing here reaches Microsoft or sends mail: a draft is only the form's own
values. A successful Create removes the module's draft (see
``teams_create_guard.teams_meeting_collection``).

The owner provisions the table using backend/sql/teams_calendar_create_drafts.sql.
"""
import json
import logging

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .teams_create_guard import module_key
from .versioning import current_actor

logger = logging.getLogger(__name__)
TABLE = 'curriculum.teams_calendar_create_drafts'

#: The create form's fields and the longest value each may hold. Anything else
#: in the body is dropped, so a draft can never carry more than the form shows.
FIELDS = {
    'scheduleTimeZone': 64,
    'seriesMode': 16,
    'organizerEmail': 254,
    'attendees': 60000,
    'presenters': 10000,
    'coOrganizers': 10000,
    'details': 8000,
    'durationMinutes': 8,
    'lobbyBypass': 64,
    'recording': 64,
    'spokenLanguage': 32,
    'meetingType': 64,
}

#: The additional week meeting form: its own fields, title and week included.
WEEK_FIELDS = {
    'weekId': 128,
    'title': 300,
    'organizerEmail': 254,
    'date': 10,
    'startTime': 5,
    'durationMinutes': 8,
    'details': 8000,
    'scheduleTimeZone': 64,
    'lobbyBypass': 64,
    'recording': 64,
    'spokenLanguage': 32,
    'presenters': 10000,
    'coOrganizers': 10000,
    'attendees': 60000,
}
KINDS = {'calendar': FIELDS, 'week': WEEK_FIELDS}

UNAVAILABLE = ('Saving a draft is not available until the owner applies '
               'backend/sql/teams_calendar_create_drafts.sql. Nothing was saved.')


def available():
    if connection.vendor != 'postgresql':
        return False
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', [TABLE])
        return bool(cursor.fetchone()[0])


def clean_form(value, fields=FIELDS):
    if not isinstance(value, dict):
        return None
    form = {}
    for field, limit in fields.items():
        item = value.get(field)
        if item is None:
            continue
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            return None
        form[field] = str(item)[:limit]
    return form


def read_draft(key, fields=FIELDS):
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT form, updated_by_email, updated_by_name, updated_at FROM {TABLE} WHERE module_key = %s', [key])
        row = cursor.fetchone()
    if not row:
        return None
    form = row[0]
    if isinstance(form, str):
        form = json.loads(form)
    return {'form': clean_form(form, fields) or {}, 'updatedByEmail': row[1] or '', 'updatedByName': row[2] or '',
            'updatedAt': row[3].isoformat() if row[3] else ''}


def clear_draft(key):
    """Best effort, for after a successful Create: a stale draft is only a prefill."""
    try:
        if key and available():
            with connection.cursor() as cursor:
                cursor.execute(f'DELETE FROM {TABLE} WHERE module_key = %s', [key])
    except Exception:
        logger.exception('The Teams create draft could not be removed after Create.')


@csrf_exempt
@transaction.non_atomic_requests
def teams_create_draft(request):
    """GET, PUT or DELETE the saved create form for one module."""
    from . import views as v
    if request.method not in ('GET', 'PUT', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    payload = v.json_body(request) if request.method == 'PUT' else {}
    payload = payload if isinstance(payload, dict) else {}
    key = module_key(v, {'moduleCatalogueId': payload.get('moduleCatalogueId') or request.GET.get('moduleCatalogueId')})
    if not key:
        return JsonResponse({'error': 'Name the module whose draft this is.'}, status=400)
    kind = str(payload.get('kind') or request.GET.get('kind') or 'calendar')
    if kind not in KINDS:
        return JsonResponse({'error': 'Unknown draft kind.'}, status=400)
    fields = KINDS[kind]
    # The module calendar keeps the key it always had; other forms are prefixed.
    key = key if kind == 'calendar' else f'{kind}:{key}'
    if not available():
        if request.method == 'GET':
            # Reading is only a prefill; with no table there is simply no draft.
            return JsonResponse({'draft': None, 'available': False})
        return JsonResponse({'error': UNAVAILABLE, 'code': 'teams_create_drafts_unavailable'}, status=503)
    if request.method == 'GET':
        return JsonResponse({'draft': read_draft(key, fields), 'available': True})
    if request.method == 'DELETE':
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM {TABLE} WHERE module_key = %s', [key])
        return JsonResponse({'draft': None, 'available': True})
    form = clean_form(payload.get('form'), fields)
    if form is None:
        return JsonResponse({'error': 'The draft could not be read. Nothing was saved.'}, status=400)
    actor = current_actor()
    with connection.cursor() as cursor:
        cursor.execute(f'''INSERT INTO {TABLE} (module_key, form, updated_by_email, updated_by_name, updated_at)
            VALUES (%s, %s::jsonb, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (module_key) DO UPDATE SET form = EXCLUDED.form,
                updated_by_email = EXCLUDED.updated_by_email, updated_by_name = EXCLUDED.updated_by_name,
                updated_at = EXCLUDED.updated_at''',
                       [key, json.dumps(form), str(actor.get('email') or '')[:254], str(actor.get('name') or '')[:254]])
    return JsonResponse({'draft': read_draft(key, fields), 'available': True})
