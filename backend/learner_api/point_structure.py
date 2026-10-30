"""Display-only point labels; never change component identity or eligibility."""
from .presentation_json import presentation_object


def point_number(settings):
    if not isinstance(settings, dict):
        return None
    value = settings.get('pointNumber')
    if value is None:
        legacy = presentation_object(settings.get('legacySettings'), max_layers=2)
        if not legacy:
            legacy = presentation_object(settings.get('_pointLegacySettings'), max_layers=2)
        value = legacy.get('pointNumber')
    return value if type(value) is int and 0 < value <= 9007199254740991 else None


def point_projection(settings):
    value = point_number(settings)
    return {'pointNumber': value} if value is not None else {}


# Object settings stay small. A native legacy JSON string needs Python decoding
# (including malformed-input handling); the internal fallback never leaves the
# component projection and is included only when no top-level value exists.
POINT_SETTINGS_SQL = """jsonb_build_object('pointNumber',coalesce(
    nullif(settings_json->'pointNumber','null'::jsonb),settings_json->'legacySettings'->'pointNumber'),
    '_pointLegacySettings',CASE WHEN nullif(settings_json->'pointNumber','null'::jsonb) IS NULL
    AND jsonb_typeof(settings_json->'legacySettings')='string'
    THEN settings_json->'legacySettings' ELSE NULL END)"""
