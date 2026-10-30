"""Read a shared live card's booked source occurrence without moving any rows.

Shared module authoring and the join redirect pair live cards by authored
position, not by occurrence number. Resolve that source card's explicit meeting
identity before reading its clock: reading weeks can differ between the modules.
"""
from datetime import timezone
from zoneinfo import ZoneInfo

from django.db import connections

from curriculum_api.session_component_identity import TIMEZONE_ALIASES


SHARED_DELIVERY_SQL = """WITH RECURSIVE chain(target, source, path) AS (
    SELECT module_catalogue_id, btrim(teams_shared_source_module_id), ARRAY[module_catalogue_id]::text[]
    FROM curriculum.modules WHERE module_catalogue_id=ANY(%s)
      AND coalesce(btrim(teams_shared_source_module_id),'')<>''
    UNION ALL
    SELECT chain.target,btrim(m.teams_shared_source_module_id),chain.path || chain.source::text
    FROM chain JOIN curriculum.modules m ON m.module_catalogue_id=chain.source
    WHERE coalesce(btrim(m.teams_shared_source_module_id),'')<>''
      AND NOT chain.source=ANY(chain.path) AND cardinality(chain.path)<6
), owners AS (
    SELECT chain.target,chain.source FROM chain
    JOIN curriculum.modules m ON m.module_catalogue_id=chain.source
    WHERE coalesce(btrim(m.teams_shared_source_module_id),'')=''
      AND NOT chain.source=ANY(chain.path)
), cards AS (
    SELECT c.id,c.module_catalogue_id,
      c.settings_json->>'teamsOccurrenceId' AS occurrence_id,
      c.settings_json->>'teamsLiveSessionId' AS series_id,
      c.settings_json->>'teamsSessionNumber' AS session_number,
      c.settings_json->>'extraTeamsLiveSessionId' AS extra_series_id,
      c.settings_json->>'extraTeamsMeetingUrl' AS extra_url,
      c.settings_json->>'liveSessionLinkOverride' AS link_override,
      row_number() OVER (PARTITION BY c.module_catalogue_id
        ORDER BY w.display_order,w.week_number,w.id,c.display_order,c.id) AS position
    FROM curriculum.components c JOIN curriculum.weeks w
      ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
    WHERE c.module_catalogue_id IN (SELECT target FROM owners UNION SELECT source FROM owners)
      AND c.type IN ('live_session','live-session')
      AND c.deleted_at IS NULL AND NOT coalesce(c.is_programme_deleted,false)
      AND coalesce(c.library_state,'')<>'library'
      AND w.deleted_at IS NULL AND NOT coalesce(w.is_programme_deleted,false)
      AND coalesce(w.library_state,'')<>'library'
)
SELECT alias.id,s.id,o.id,o.session_number,o.scheduled_start,o.scheduled_end,s.timezone,
       coalesce(nullif(btrim(alias.link_override),''),nullif(btrim(source.link_override),''),
                nullif(btrim(o.join_url),''),nullif(btrim(s.join_url),''))
FROM owners JOIN cards alias ON alias.module_catalogue_id=owners.target
JOIN cards source ON source.module_catalogue_id=owners.source AND source.position=alias.position
JOIN curriculum.live_sessions s ON s.module_catalogue_id=owners.source
  AND s.id=CASE WHEN coalesce(source.extra_url,'')<>'' AND coalesce(source.extra_series_id,'')<>''
               THEN source.extra_series_id ELSE source.series_id END
JOIN curriculum.live_session_occurrences o ON o.live_session_id=s.id
  AND CASE WHEN coalesce(source.extra_url,'')<>'' AND coalesce(source.extra_series_id,'')<>''
           THEN o.session_number=1
           WHEN coalesce(source.occurrence_id,'')<>'' THEN o.id=source.occurrence_id
           ELSE o.session_number::text=source.session_number END
WHERE alias.id=ANY(%s)
  AND NOT (coalesce(alias.extra_url,'')<>'' AND coalesce(alias.extra_series_id,'')<>'')
  AND lower(coalesce(s.status,'')) NOT IN ('cancelled','canceled','deleted','superseded','failed')
  AND lower(coalesce(o.status,'')) NOT IN ('cancelled','canceled','deleted','superseded','failed')
"""


def read_shared_live_delivery(module_ids, component_ids, *, cursor=None):
    if not module_ids or not component_ids:
        return {}
    if cursor is None:
        with connections['enrolment'].cursor() as cursor:
            return read_shared_live_delivery(module_ids, component_ids, cursor=cursor)
    cursor.execute(SHARED_DELIVERY_SQL, [sorted(set(module_ids)), sorted(set(component_ids))])
    rows = cursor.fetchall()
    result = {}
    for cid, series, occurrence, number, start, end, zone, link in rows:
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
        local = start.astimezone(ZoneInfo(TIMEZONE_ALIASES.get(zone, zone or 'Europe/London')))
        result[cid] = {
            'teamsLiveSessionId': series, 'teamsOccurrenceId': occurrence,
            'teamsSessionNumber': number, 'sessionDate': local.date().isoformat(),
            'sessionTime': local.strftime('%H:%M'), 'sessionDateTimeUtc': start.astimezone(timezone.utc).isoformat(),
            'durationMinutes': (end - start).total_seconds() / 60,
            'liveSessionUrl': link,
        }
    return result
