"""Idempotent creation of enrolment."Enrolment_Documents".

One row per generated compliance document held in
AZURE_ENROLMENT_DOCS_CONTAINER, recording its Azure path and its type (see
DOC_TYPES in documents.py — Extended ILR today, seven more to come).

Created with CREATE TABLE IF NOT EXISTS so it is safe to call on any request
path, mirroring learner_api.evidence_tables.ensure_evidence_tables. The Neon
schemas are owned outside Django, so this is raw SQL rather than a migration.

Prefer `python manage.py apply_enrolment_documents_table` at deploy time. This
function stays as the safety net for a database that never had it run, but
creating tables during a user request is how the incomplete table below shipped
in the first place: the CREATE lacked the six signature columns that
documents.SELECT_COLS reads on every query, and the two commands that added
them both bail out when the table is absent -- so the request path silently
created a table that every subsequent read then failed against.

Learners are addressed by (Learner_kind, Learner_id) — a pairing that dates from
when the directory spanned two learner tables. Both kinds now share
enrolment."Created_users", so Learner_id alone is unique; Learner_kind is kept so
existing rows keep resolving.

Rows are append-only per generation: regenerating a document inserts a new row
with a new timestamped blob, so a previously signed-and-filed copy is never
overwritten. "The current ILR" is therefore the newest row of that Doc_type.
"""
from django.db import connections

_READY = False

# Columns added after the table's first release. Every one is also in the CREATE
# above, so a new database gets them outright; these ALTERs exist only to bring
# an older table up to date. Keep the two lists in step -- tests_document_schema
# asserts they agree, and asserts both cover what documents.SELECT_COLS reads.
_PATCH_COLUMNS = (
    ("Learner_signature", "text"),
    ("Learner_signed_name", "text"),
    ("Learner_signed_at", "timestamptz"),
    ("Employer_signature", "text"),
    ("Employer_signed_name", "text"),
    ("Employer_signed_at", "timestamptz"),
    ("Updated_at", "timestamptz not null default now()"),
)


def ensure_enrolment_documents_table():
    global _READY
    if _READY:
        return
    with connections["enrolment"].cursor() as cur:
        cur.execute('create schema if not exists enrolment')
        cur.execute(
            '''
            create table if not exists enrolment."Enrolment_Documents" (
                id             uuid primary key,
                "Learner_kind" varchar(32)  not null,
                "Learner_id"   bigint       not null,
                "Learner_name" text,
                "Doc_type"     varchar(64)  not null,
                "Doc_name"     varchar(512) not null,
                "Container"    varchar(128) not null,
                "Blob_name"    varchar(1024) not null unique,
                "Doc_path"     text         not null,
                "Content_type" varchar(255),
                "Size_bytes"   bigint,
                "Signed"       boolean      not null default false,
                -- Per-party sign-off. "Signed" stays as the summary flag the
                -- documents list reads, and means "every party this document
                -- needs has signed".
                "Learner_signature"    text,
                "Learner_signed_name"  text,
                "Learner_signed_at"    timestamptz,
                "Employer_signature"   text,
                "Employer_signed_name" text,
                "Employer_signed_at"   timestamptz,
                "Generated_at" timestamptz  not null default now(),
                -- replace_document_file swaps Blob_name/Doc_path/Size_bytes in
                -- place, which Generated_at alone cannot distinguish.
                "Updated_at"   timestamptz  not null default now()
            )
            '''
        )
        # Self-healing for databases created before the columns above were part
        # of the CREATE. documents.SELECT_COLS reads all six signature columns on
        # every query, so a table missing them fails with UndefinedColumn on the
        # first read -- and the two commands that used to add them both bail out
        # when the table is absent, which is precisely the deadlock this avoids.
        for column, ddl in _PATCH_COLUMNS:
            cur.execute(
                f'alter table enrolment."Enrolment_Documents" '
                f'add column if not exists "{column}" {ddl}'
            )
        cur.execute(
            'create index if not exists idx_enrolment_docs_learner '
            'on enrolment."Enrolment_Documents" ("Learner_kind", "Learner_id")'
        )
        cur.execute(
            'create index if not exists idx_enrolment_docs_type '
            'on enrolment."Enrolment_Documents" ("Doc_type")'
        )
    _READY = True
