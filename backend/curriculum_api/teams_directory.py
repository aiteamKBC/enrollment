"""Staff-only, bounded Entra directory lookup for Teams role pickers."""
import logging
import re
from urllib.parse import urlencode

from django.http import JsonResponse
from django.views.decorators.http import require_GET
from login.permissions import require_role

logger = logging.getLogger(__name__)


def directory_query(value, *, include_guests=False):
    query = str(value or '').strip()
    if len(query) < 2:
        return None
    if len(query) > 80 or any(ord(char) < 32 for char in query):
        raise ValueError('Search using 2 to 80 characters.')
    literal = query.replace("'", "''")
    names = ('displayName', 'givenName', 'surname', 'mail', 'userPrincipalName')
    match = ' or '.join(f"startswith({name},'{literal}')" for name in names)
    user_types = "(userType eq 'Member' or userType eq 'Guest')" if include_guests else "userType eq 'Member'"
    return 'users?' + urlencode({
        '$select': 'id,displayName,mail,userPrincipalName,userType,accountEnabled',
        '$filter': f"accountEnabled eq true and {user_types} and ({match})", '$top': 12,
    })


def directory_people(data, *, include_guests=False):
    if not isinstance(data, dict) or not isinstance(data.get('value'), list):
        raise RuntimeError('The directory response could not be read.')
    people, seen = [], set()
    allowed_types = {'Member', 'Guest'} if include_guests else {'Member'}
    for user in data['value'][:12]:
        if not isinstance(user, dict):
            continue
        if user.get('accountEnabled') is not True or user.get('userType') not in allowed_types:
            continue
        email = str(user.get('mail') or user.get('userPrincipalName') or '').strip()
        if not user.get('id') or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) or email.lower() in seen:
            continue
        seen.add(email.lower())
        people.append({'id': user['id'], 'name': str(user.get('displayName') or email), 'email': email})
    return {'people': people, 'hasMore': bool(data.get('@odata.nextLink'))}


@require_role('admin', 'staff')
@require_GET
def search_teams_directory(request):
    from coach_api.views import microsoft_graph_request

    try:
        include_guests = request.GET.get('includeGuests') == '1'
        path = directory_query(request.GET.get('q'), include_guests=include_guests)
        result = (directory_people(microsoft_graph_request('GET', path), include_guests=include_guests)
                  if path else {'people': [], 'hasMore': False})
        response = JsonResponse(result)
    except ValueError as exc:
        response = JsonResponse({'error': str(exc)}, status=400)
    except (RuntimeError, OSError) as exc:
        # The browser gets a plain message; Graph's code and request id stay
        # server-side, where an access or token failure can be diagnosed. A
        # read timeout arrives as OSError, not RuntimeError.
        logger.warning('Entra directory search failed: %s', exc)
        denied = 'HTTP 403' in str(exc)
        message = ("The Microsoft app this LMS uses isn't allowed to search the directory. Enter a full email address."
                   if denied else 'Entra search is unavailable. Retry or enter a full email address.')
        response = JsonResponse({'error': message}, status=502)
    response['Cache-Control'] = 'no-store, private'
    return response
