"""Validated display-only YouTube alternative; never replace primary audio."""
import re
from urllib.parse import parse_qs, urlsplit
from .presentation_json import presentation_object


def youtube_alternative_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\x00-\x20\x7f]", value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or url.username or url.password or url.port:
            return None
        if url.hostname == 'youtu.be' and re.fullmatch(r'/[A-Za-z0-9_-]{11}', url.path):
            video_id = url.path[1:]
        elif url.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com') and url.path == '/watch':
            ids = parse_qs(url.query).get('v', [])
            video_id = ids[0] if len(ids) == 1 else None
        else:
            return None
        return 'https://www.youtube.com/watch?v=' + video_id if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id) else None
    except ValueError:
        return None


def audio_alternative_projection(settings, component_type, audio_url):
    if component_type not in ('podcast', 'audio') or not audio_url or not isinstance(settings, dict):
        return {}
    value = settings.get('alternativeYouTubeUrl')
    if value is None:
        legacy = presentation_object(settings.get('legacySettings'), max_layers=2)
        if not legacy:
            legacy = presentation_object(settings.get('_audioAlternativeLegacy'), max_layers=2)
        value = legacy.get('alternativeYouTubeUrl')
    url = youtube_alternative_url(value)
    return {'alternativeYouTubeUrl': url} if url else {}


AUDIO_ALTERNATIVE_SETTINGS_SQL = """jsonb_build_object('alternativeYouTubeUrl',coalesce(
    nullif(settings_json->'alternativeYouTubeUrl','null'::jsonb),settings_json->'legacySettings'->'alternativeYouTubeUrl'),
    '_audioAlternativeLegacy',CASE WHEN nullif(settings_json->'alternativeYouTubeUrl','null'::jsonb) IS NULL
    AND jsonb_typeof(settings_json->'legacySettings')='string'
    THEN settings_json->'legacySettings' ELSE NULL END)"""
