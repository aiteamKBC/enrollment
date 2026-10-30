"""Write the learner-only live-session register without changing Teams evidence."""


REGISTER_TABLE = 'curriculum.live_session_learner_attendance'


def register_params(row):
    """Return the stable register values for one assigned module learner."""

    return (
        row['occurrence_id'],
        row['live_session_id'],
        row['module_catalogue_id'],
        row['learner_profile_id'],
        str(row.get('learner_email') or '').strip().casefold(),
        str(row.get('learner_name') or '').strip(),
        row['attendance_status'],
        str(row.get('recovery_status') or 'none').strip(),
        max(0, int(row.get('attended_seconds') or 0)),
        str(row.get('attendance_report_id') or '').strip(),
        row.get('first_join_at'),
        row.get('last_leave_at'),
        row['calculated_at'],
    )


def upsert_live_session_register(cursor, rows):
    """Upsert current results; never delete or rewrite raw Teams attendance."""

    params = [register_params(row) for row in rows]
    if not params:
        return 0
    cursor.executemany(
        f'''INSERT INTO {REGISTER_TABLE} (
            occurrence_id, live_session_id, module_catalogue_id,
            learner_profile_id, learner_email, learner_name,
            attendance_status, recovery_status, attended_seconds, attendance_report_id,
            first_join_at, last_leave_at, calculated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (occurrence_id, learner_profile_id, module_catalogue_id) DO UPDATE SET
            live_session_id = EXCLUDED.live_session_id,
            learner_email = EXCLUDED.learner_email,
            learner_name = EXCLUDED.learner_name,
            attendance_status = EXCLUDED.attendance_status,
            recovery_status = EXCLUDED.recovery_status,
            attended_seconds = EXCLUDED.attended_seconds,
            attendance_report_id = EXCLUDED.attendance_report_id,
            first_join_at = EXCLUDED.first_join_at,
            last_leave_at = EXCLUDED.last_leave_at,
            calculated_at = EXCLUDED.calculated_at,
            updated_at = CURRENT_TIMESTAMP''',
        params,
    )
    return len(params)
