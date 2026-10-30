"""Read attendance from the meeting a replaced module link actually opens.

``Replace Teams links`` deliberately changes only authored component links.  It
does not move Microsoft ownership or copy provider ids.  A destination module
therefore keeps its own occurrence identity while its learners attend another
module's meeting.  This lateral join resolves that source occurrence only when
the stored override, destination occurrence and source session number identify
exactly one candidate.  Ambiguity leaves the destination on its own evidence.
"""


REPLACED_ATTENDANCE_LATERAL_SQL = r'''
LEFT JOIN LATERAL (
    SELECT min(candidate.occurrence_id) AS occurrence_id,
           max(candidate.attendance_report_id) AS attendance_report_id
    FROM (
        SELECT DISTINCT source_o.id AS occurrence_id,
               source_o.attendance_report_id
        FROM curriculum.components target_c
        LEFT JOIN curriculum.weeks target_w
          ON target_w.id=target_c.week_id
         AND target_w.module_catalogue_id=target_c.module_catalogue_id
        JOIN curriculum.live_session_occurrences source_o
          ON source_o.session_number=o.session_number
        JOIN curriculum.live_sessions source_s
          ON source_s.id=source_o.live_session_id
        WHERE target_c.module_catalogue_id=s.module_catalogue_id
          AND target_c.deleted_at IS NULL
          AND NOT coalesce(target_c.is_programme_deleted,false)
          AND (target_w.id IS NULL OR
               (target_w.deleted_at IS NULL AND NOT coalesce(target_w.is_programme_deleted,false)))
          AND lower(replace(coalesce(target_c.type,''),'-','_'))='live_session'
          AND (
              coalesce(target_c.settings_json->>'teamsOccurrenceId',
                       target_c.settings_json #>> '{legacySettings,teamsOccurrenceId}')=o.id
              OR (
                  target_c.settings_json->>'teamsLiveSessionId'=s.id
                  AND target_c.settings_json->>'teamsSessionNumber'=o.session_number::text
              )
          )
          AND nullif(btrim(target_c.settings_json->>'liveSessionLinkOverride'),'') IS NOT NULL
          AND nullif(btrim(target_c.settings_json->>'liveSessionLinkOverride'),'')=
              coalesce(nullif(btrim(source_o.join_url),''),nullif(btrim(source_s.join_url),''))
          AND source_s.module_catalogue_id IS DISTINCT FROM s.module_catalogue_id
          AND lower(btrim(source_s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
          AND lower(btrim(source_o.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
    ) candidate
    HAVING count(DISTINCT candidate.occurrence_id)=1
) replaced_attendance ON true
'''
