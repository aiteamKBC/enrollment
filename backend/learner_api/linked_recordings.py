"""A session recording linked to a live session, as the learner sees it.

The Module Builder makes a "Session Recording" component beside every new live
session and links the two; an author can also add one by hand and link it to
any live session -- another week of the same module, or another group's module.
The link lives on the recording (frontend: ``shared/entities/liveSessionRecordingLink.ts``):

- ``linkedLiveSessionComponentId`` -- the live-session component's id;
- ``linkedLiveSessionModuleId`` -- the module holding it, empty for this module.

A link to a component that is gone, or is not a live session, reads as unlinked.

Linked, the recording plays its live session's Teams recording, so it takes
the session's series, session number and date -- the same three fields a
recorded-event video carries, which the learner runner already plays. Playing
another group's recording is allowed by ``curriculum_api.linked_recording_access``
for exactly the linked sessions, and for nothing else (no Join, no attendance).

With ``LEARNER_MERGE_LINKED_RECORDINGS=true`` a recording linked to a session in
the SAME module is not listed separately: that session's own page already shows
the recording, so the learner sees one activity. A recording of another module's
session always stays listed -- it is the learner's only way to it.
"""
import os

LINK_KEY = 'linkedLiveSessionComponentId'
LINK_MODULE_KEY = 'linkedLiveSessionModuleId'
MODULE_KEY = '_moduleId'

# Carried through every compact/identity projection of components, so the
# decision is the same on every learner screen.
LINKED_RECORDING_SETTINGS_SQL = (f"jsonb_build_object('{LINK_KEY}',settings_json->'{LINK_KEY}',"
                                 f"'{LINK_MODULE_KEY}',settings_json->'{LINK_MODULE_KEY}')")

# Live sessions in other modules that a learner's recordings link to. Same
# series/number rule as the learner's own cards: an additional week meeting is
# its own single-event series.
REMOTE_SESSIONS_SQL = '''SELECT c.module_catalogue_id, c.id,
        CASE WHEN coalesce(c.settings_json->>'extraTeamsMeetingUrl','')<>''
             THEN c.settings_json->>'extraTeamsLiveSessionId' ELSE c.settings_json->>'teamsLiveSessionId' END AS series_id,
        CASE WHEN coalesce(c.settings_json->>'extraTeamsMeetingUrl','')<>'' THEN '1'
             ELSE coalesce(nullif(c.settings_json->>'teamsSessionNumber',''), o.session_number::text) END AS session_number,
        c.settings_json->>'sessionDate' AS session_date
    FROM curriculum.components c
    JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
    LEFT JOIN curriculum.live_session_occurrences o ON o.id=c.settings_json->>'teamsOccurrenceId'
    WHERE c.id=ANY(%s) AND lower(replace(coalesce(c.type,''),'-','_'))='live_session'
      AND c.deleted_at IS NULL AND NOT coalesce(c.is_programme_deleted,false)
      AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)'''


def merge_enabled():
    return os.environ.get('LEARNER_MERGE_LINKED_RECORDINGS', 'false').strip().lower() == 'true'


def _type(value):
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_')


def _text(value):
    return str(value).strip() if value is not None else ''


def _link(row):
    """(module id, component id) a recording row links to, or None."""
    if _type(row.get('type')) != 'recording_placeholder':
        return None
    component_id = _text(row.get(LINK_KEY))
    if not component_id:
        return None
    return _text(row.get(LINK_MODULE_KEY)) or _text(row.get(MODULE_KEY)), component_id


def remote_links(comps_by_week):
    """Component ids of live sessions in OTHER modules that recordings link to."""
    return sorted({
        link[1]
        for rows in comps_by_week.values() for row in rows
        for link in [_link(row)] if link and link[0] != _text(row.get(MODULE_KEY))
    })


def read_remote_sessions(cursor, component_ids):
    """{(module id, component id): session fields} for linked sessions in other modules."""
    if not component_ids:
        return {}
    cursor.execute(REMOTE_SESSIONS_SQL, [list(component_ids)])
    found = {}
    for module_id, component_id, series_id, number, date in cursor.fetchall():
        number = _text(number)
        found[(_text(module_id), _text(component_id))] = {
            'teamsLiveSessionId': _text(series_id),
            'teamsSessionNumber': int(number) if number.isdigit() and int(number) > 0 else None,
            'sessionDate': _text(date),
        }
    return found


def resolve_linked_recordings(comps_by_week, remote_sessions=None, *, merge=None):
    """``comps_by_week`` with every linked recording resolved, in place order kept.

    Rows are the dicts ``_resolve_from_master`` builds; each carries its own
    module under ``_moduleId`` and a recording's link under the two link keys.
    """
    merge = merge_enabled() if merge is None else merge
    remote_sessions = remote_sessions or {}
    local = {
        (_text(row.get(MODULE_KEY)), _text(row.get('componentId'))): row
        for rows in comps_by_week.values() for row in rows
        if _type(row.get('type')) == 'live_session'
    }
    resolved = {}
    for week_id, rows in comps_by_week.items():
        kept = []
        for row in rows:
            link = _link(row)
            same_module = bool(link) and link[0] == _text(row.get(MODULE_KEY))
            session = (local.get(link) if same_module else remote_sessions.get(link)) if link else None
            if session is None:
                kept.append(row)
                continue
            if merge and same_module:
                continue
            series = _text(session.get('teamsLiveSessionId'))
            number = session.get('teamsSessionNumber')
            date = _text(session.get('sessionDate'))[:10]
            recording = dict(row)
            if date:
                recording['sessionDate'] = date
            if series and number and date:
                recording['recordingSeriesId'] = series
                recording['recordingSessionNumber'] = number
                recording['recordingDate'] = date
            kept.append(recording)
        resolved[week_id] = kept
    return resolved
