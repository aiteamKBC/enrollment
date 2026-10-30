CREATE SCHEMA IF NOT EXISTS enrollment_reference;
CREATE TABLE IF NOT EXISTS enrollment_reference.aptem_status (
    "ID" bigint PRIMARY KEY,
    "Program-Status" text,
    refreshed_at timestamptz NOT NULL DEFAULT now()
);
