"""Which sessions of another group's series a learner may watch through a link.

A "Session Recording" component can be linked to a live session in a
different module (another group's delivery). The curriculum author's link is
the grant: a learner assigned to the recording's module may play and download
the recording and transcript of exactly the linked session(s) of that series.

It grants nothing else. Join stays limited to the learner's own modules (the
join endpoint never asks here), results are still filtered to the learner's own
attendance row, and watching never counts as attendance, because attendance is
read against the learner's own module occurrences only.

The link is ``settings.linkedLiveSessionComponentId`` (+ ``linkedLiveSessionModuleId``
for another module) on a recording in one of the learner's modules, naming a
live-session component whose series is the one asked for.
"""
from .session_results import read

LINKED_SESSIONS_SQL = '''SELECT s.settings_json->>'teamsLiveSessionId' AS series_id,
        s.settings_json->>'teamsSessionNumber' AS session_number,
        s.settings_json->>'teamsOccurrenceId' AS occurrence_id,
        s.settings_json->>'extraTeamsLiveSessionId' AS extra_series_id,
        s.settings_json->>'extraTeamsMeetingUrl' AS extra_meeting_url
    FROM curriculum.components r
    LEFT JOIN curriculum.weeks rw ON rw.id=r.week_id AND rw.module_catalogue_id=r.module_catalogue_id
    JOIN curriculum.components s ON s.id=r.settings_json->>'linkedLiveSessionComponentId'
        AND s.module_catalogue_id=coalesce(nullif(r.settings_json->>'linkedLiveSessionModuleId',''), r.module_catalogue_id)
    JOIN curriculum.modules m ON m.module_catalogue_id=s.module_catalogue_id
    WHERE r.module_catalogue_id=ANY(%s)
      AND lower(replace(coalesce(r.type,''),'-','_'))='recording_placeholder'
      AND r.deleted_at IS NULL AND NOT coalesce(r.is_programme_deleted,false)
      AND (rw.id IS NULL OR (rw.deleted_at IS NULL AND NOT coalesce(rw.is_programme_deleted,false)))
      AND lower(replace(coalesce(s.type,''),'-','_'))='live_session'
      AND s.deleted_at IS NULL AND NOT coalesce(s.is_programme_deleted,false)
      AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
      AND (s.settings_json->>'teamsLiveSessionId'=%s OR s.settings_json->>'extraTeamsLiveSessionId'=%s)'''


def linked_session_numbers(module_ids, series_id):
    """The session numbers of ``series_id`` linked from a recording in ``module_ids``."""
    modules = sorted({str(item) for item in module_ids if item})
    if not modules or not series_id:
        return set()
    rows = read(LINKED_SESSIONS_SQL, [modules, series_id, series_id])
    numbers, occurrence_ids = set(), set()
    for row in rows:
        if row.get('extra_meeting_url') and row.get('extra_series_id') == series_id:
            numbers.add(1)  # an additional week meeting is a single event
            continue
        if row.get('series_id') != series_id:
            continue
        number = str(row.get('session_number') or '').strip()
        if number.isdigit() and int(number) > 0:
            numbers.add(int(number))
        elif row.get('occurrence_id'):
            occurrence_ids.add(str(row['occurrence_id']))
    if occurrence_ids:
        found = read('''SELECT session_number FROM curriculum.live_session_occurrences
            WHERE live_session_id=%s AND id=ANY(%s)''', [series_id, sorted(occurrence_ids)])
        numbers.update(int(row['session_number']) for row in found if row.get('session_number'))
    return numbers


def artifact_session_number(series_id, artifact_id):
    """The session number of the recording/transcript ``artifact_id`` in ``series_id``, or None.

    Only those two kinds: the link shares what was recorded, never anything
    else a session may come to hold.
    """
    rows = read('''SELECT o.session_number FROM curriculum.live_session_artifacts a
        JOIN curriculum.live_session_occurrences o ON o.id=a.occurrence_id
        WHERE a.id=%s AND o.live_session_id=%s
          AND a.artifact_type IN ('recording','transcript')''', [artifact_id, series_id])
    return int(rows[0]['session_number']) if rows and rows[0].get('session_number') else None


def linked_artifact_allowed(series, artifact_id):
    """Whether ``series`` (as returned by learner_series) lets this artifact through."""
    numbers = series.get('linked_session_numbers')
    if numbers is None:
        return True  # the learner's own (or shared) series: unchanged rules
    return artifact_session_number(series['id'], artifact_id) in numbers
