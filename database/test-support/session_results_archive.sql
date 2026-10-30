-- Owner-run only, in Neon SQL Editor. No historical attendance is changed.
-- Prerequisites: existing curriculum live_sessions, occurrences and artifacts.
BEGIN;
CREATE TABLE IF NOT EXISTS curriculum.session_result_archive (
    artifact_id text PRIMARY KEY REFERENCES curriculum.live_session_artifacts(id) ON DELETE RESTRICT,
    occurrence_id text NOT NULL REFERENCES curriculum.live_session_occurrences(id) ON DELETE RESTRICT,
    container text NOT NULL,
    blob_name text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed')),
    transcript_text text,
    last_error text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS session_result_archive_occurrence ON curriculum.session_result_archive(occurrence_id);
CREATE TABLE IF NOT EXISTS curriculum.session_result_jobs (
    live_session_id text PRIMARY KEY REFERENCES curriculum.live_sessions(id) ON DELETE RESTRICT,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','complete','failed')),
    requested_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    force_refresh boolean NOT NULL DEFAULT false,
    lease_id text,
    last_error text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS session_result_jobs_due ON curriculum.session_result_jobs(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS session_join_launch_viewer
    ON curriculum.live_session_join_launches(lower(btrim(viewer_email)), live_session_id, occurrence_id);
COMMIT;

-- Read-only verification:
SELECT table_schema, table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema='curriculum'
  AND table_name IN ('session_result_archive','session_result_jobs')
ORDER BY table_name, ordinal_position;
