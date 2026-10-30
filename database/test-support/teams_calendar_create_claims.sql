-- One in-flight "Create Teams calendar" per module, shared by every worker.
-- Creates a claim table only; it does not send messages or modify meetings.
-- A claim is taken with one conditional INSERT ... ON CONFLICT before Microsoft
-- is called, and records the outcome when the request finishes. A claim still
-- 'creating' after lease_until (a crashed or killed worker) reads as uncertain
-- and is never retried automatically.
CREATE TABLE IF NOT EXISTS curriculum.teams_calendar_create_claims (
    module_key varchar(300) PRIMARY KEY,
    token varchar(64) NOT NULL,
    status varchar(16) NOT NULL
        CHECK (status IN ('creating', 'done', 'uncertain')),
    outcome_status integer,
    outcome_code varchar(80) NOT NULL DEFAULT '',
    live_session_id varchar(128) NOT NULL DEFAULT '',
    claimed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_until timestamptz NOT NULL,
    finished_at timestamptz
);

-- Read-only verification (run separately after creation).
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'curriculum' AND table_name = 'teams_calendar_create_claims'
ORDER BY ordinal_position;
