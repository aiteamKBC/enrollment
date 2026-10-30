"""Resolve the KSB source a programme has applied: a KSB profile or a standard.

``curriculum.programmes.ksb_profile_source_id`` holds either a manual KSB
profile id ('KSBP-...', sometimes written 'profile:KSBP-...') or a Skills
England standard ('standard:st0596-v1-0'). Every consumer -- AI marking, the
Skills Radar, the review form, the learner KSB list -- must read the list from
whichever of the two is applied.

An *applied* profile is resolved even when it has been archived
(is_active = false): the programme still names it, and historical records
(signed Skills Radar answers, reviews) were made against its items. Only
*discovery* -- guessing a profile from a programme name when nothing is
applied -- is limited to active profiles, and that stays with the callers.

Item shape (profile items and standard items alike)::

    {'id', 'code', 'type', 'number', 'description', 'title'}

``type`` is the single letter 'K', 'S' or 'B'. A standard item's ``id`` is
'<standard id>-<code>' ('st0596-v1-0-K1'): stable, unique within the standard,
and its last segment is the code, which is what Skills Radar consumers such as
board_wizard._ksb_code() read back out of a stored answer id.
"""
import json
from dataclasses import dataclass, field

from django.db import DatabaseError, connections

PROFILE = 'profile'
STANDARD = 'standard'


@dataclass
class ResolvedKsbSource:
    kind: str = ''            # 'profile' | 'standard' | ''
    source_id: str = ''       # 'KSBP-...' or 'st0596-v1-0'
    label: str = ''
    active: bool = True       # False for an archived profile
    standard_ref: str = ''    # 'st0596' when known
    items: list = field(default_factory=list)

    @property
    def source_value(self):
        if self.kind == STANDARD:
            return f'standard:{self.source_id}'
        return self.source_id


def _s(value):
    return '' if value is None else str(value).strip()


def parse_source(value):
    """('profile'|'standard'|'', id) for a stored source value."""
    text = _s(value)
    if not text:
        return '', ''
    lowered = text.lower()
    if lowered.startswith('standard:'):
        return STANDARD, _s(text.split(':', 1)[1]).lower()
    if lowered.startswith(('profile:', 'framework:')):
        text = _s(text.split(':', 1)[1])
    if text.lower().startswith('ksb-'):
        text = text[4:]
    return (PROFILE, text) if text else ('', '')


def _letter(value, code=''):
    raw = _s(value).upper()
    if raw[:1] in {'K', 'S', 'B'}:
        return raw[:1]
    return _s(code).upper()[:1] if _s(code).upper()[:1] in {'K', 'S', 'B'} else ''


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return []
    return value if value is not None else []


def profile_items(profile_id, raw_items):
    """Normalise a ksb_profiles.ksb_items array to the shared item shape."""
    items = []
    for index, item in enumerate(_decode(raw_items) if isinstance(_decode(raw_items), list) else []):
        if not isinstance(item, dict):
            continue
        type_letter = _letter(item.get('type'), item.get('code'))
        raw_code = _s(item.get('code') or item.get('number'))
        code = raw_code.upper() if raw_code[:1].upper() in {'K', 'S', 'B'} else f'{type_letter}{raw_code}'
        if not code:
            continue
        description = _s(item.get('description') or item.get('title'))
        items.append({
            'id': _s(item.get('id')) or f'{profile_id}-{index}',
            'code': code,
            'type': type_letter or code[:1],
            'number': _s(item.get('number')) or code[1:],
            'description': description,
            'title': _s(item.get('title')) or description,
        })
    return items


def fetch_profile(profile_id, using='default'):
    """The profile row an applied source names, archived or not, or None."""
    profile_id = _s(profile_id)
    if not profile_id:
        return None
    try:
        with connections[using].cursor() as cursor:
            cursor.execute(
                'SELECT id, name, ksb_items, COALESCE(is_active, true), standard_source_id '
                'FROM curriculum.ksb_profiles '
                'WHERE id = %s OR ksb_profile_id = %s '
                'ORDER BY COALESCE(is_active, true) DESC, updated_at DESC NULLS LAST LIMIT 1',
                [profile_id, profile_id],
            )
            row = cursor.fetchone()
    except DatabaseError:
        return None
    if not row:
        return None
    return {'id': row[0], 'name': row[1], 'ksb_items': row[2], 'is_active': bool(row[3]),
            'standard_source_id': row[4]}


def fetch_standard_rows(standard_id, using='default'):
    """Rows of curriculum.standard_ksbs for 'st0596-v1-0', in the standard's order."""
    standard_id = _s(standard_id).lower()
    if not standard_id:
        return []
    try:
        with connections[using].cursor() as cursor:
            cursor.execute(
                "SELECT id, ksb_code, ksb_type, ksb_description, standard_title, standard_ref "
                "FROM curriculum.standard_ksbs "
                "WHERE lower(standard_ref) || '-v' || replace(standard_version, '.', '-') = %s "
                "ORDER BY id",
                [standard_id],
            )
            rows = cursor.fetchall()
    except DatabaseError:
        return []
    return [{'id': r[0], 'ksb_code': r[1], 'ksb_type': r[2], 'ksb_description': r[3],
             'standard_title': r[4], 'standard_ref': r[5]} for r in rows]


def standard_items(standard_id, rows):
    """Standard rows in the shared item shape, one item per code (first row wins)."""
    items, seen = [], set()
    for row in rows:
        code = _s(row.get('ksb_code')).upper()
        if not code or code in seen:
            continue
        seen.add(code)
        description = _s(row.get('ksb_description'))
        items.append({
            'id': f'{standard_id}-{code}',
            'code': code,
            'type': _letter(row.get('ksb_type'), code) or code[:1],
            'number': code[1:],
            'description': description,
            'title': description,
        })
    return items


def resolve_source(value, using='default', *, fetch_profile_fn=None, fetch_standard_fn=None):
    """The applied KSB source and its items. Empty items mean nothing resolvable.

    ``fetch_profile_fn`` / ``fetch_standard_fn`` replace the database reads in
    tests.
    """
    kind, source_id = parse_source(value)
    fetch_profile_fn = fetch_profile_fn or (lambda pid: fetch_profile(pid, using))
    fetch_standard_fn = fetch_standard_fn or (lambda sid: fetch_standard_rows(sid, using))
    if kind == STANDARD:
        rows = fetch_standard_fn(source_id)
        title = _s(rows[0].get('standard_title')) if rows else ''
        ref = _s(rows[0].get('standard_ref')).lower() if rows else source_id.split('-', 1)[0]
        return ResolvedKsbSource(kind=STANDARD, source_id=source_id, label=title or source_id.upper(),
                                 active=True, standard_ref=ref, items=standard_items(source_id, rows))
    if kind == PROFILE:
        row = fetch_profile_fn(source_id)
        if not row:
            return ResolvedKsbSource(kind=PROFILE, source_id=source_id, active=False)
        return ResolvedKsbSource(
            kind=PROFILE, source_id=_s(row.get('id')) or source_id, label=_s(row.get('name')),
            active=bool(row.get('is_active', True)),
            standard_ref=_s(row.get('standard_source_id')).lower().split('-', 1)[0],
            items=profile_items(_s(row.get('id')) or source_id, row.get('ksb_items')),
        )
    return ResolvedKsbSource()
