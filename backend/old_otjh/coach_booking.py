"""Published booking links, matched to the source-assigned coach email."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings


# Public URLs supplied by the college. Keep the full catalogue (including the
# session-specific links) with the app so deployments need no database import.
COACH_BOOKINGS = json.loads(
    (Path(__file__).parent / 'data' / 'coach_bookings.json').read_text(encoding='utf-8')
)['coaches']


def booking_url(email):
    email = str(email or '').strip().casefold()
    if not email:
        return None
    configured = getattr(settings, 'OLD_OTJH_COACH_BOOKING_URLS', {})
    if isinstance(configured, str):
        try:
            configured = json.loads(configured)
        except ValueError:
            return None
    if not isinstance(configured, dict):
        return None
    matches = [value for key, value in configured.items()
               if isinstance(key, str) and key.strip().casefold() == email]
    if not matches:
        matches = [coach['booking_page_url'] for coach in COACH_BOOKINGS
                   if coach['email'].strip().casefold() == email]
    # Ambiguous configuration must not send the learner to another coach.
    if len(matches) != 1 or not isinstance(matches[0], str):
        return None
    url = matches[0].strip()
    if not url or len(url) > 2048 or any(char.isspace() or ord(char) < 32 for char in url) or '\\' in url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        parsed.port  # Validate a supplied port without contacting the destination.
    except ValueError:
        return None
    return url
