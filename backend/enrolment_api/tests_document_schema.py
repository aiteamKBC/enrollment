"""Schema tests for enrolment."Enrolment_Documents".

P0-1 in ENROLMENT_GAP_ANALYSIS.md: the CREATE TABLE in document_tables.py made
13 columns, none of them signature columns, while documents.SELECT_COLS read six
signature columns on every query. The columns came only from two commands that
each bail out when the table is absent, so a fresh deployment auto-created a
table that every subsequent read failed against -- and employer_portal swallows
the DatabaseError, so the failure was invisible.

Nothing checked that the DDL and the query agreed. These tests do, by parsing
both as text. SimpleTestCase with no database, so they run under
DJANGO_USE_SQLITE and in CI without a Neon connection.
"""
import re

from django.test import SimpleTestCase

from .document_tables import _PATCH_COLUMNS, ensure_enrolment_documents_table
from .documents import SELECT_COLS

# The CREATE lives inside the function body; read it from the source rather than
# duplicating it here, so the test cannot drift from what actually runs.
import inspect

_SOURCE = inspect.getsource(ensure_enrolment_documents_table)


def _created_columns():
    """Quoted identifiers defined in the create table block."""
    create = _SOURCE.split("create table if not exists", 1)[1]
    create = create.split("'''", 1)[0]
    return {m.group(1) for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]*)"\s+\w', create)}


def _selected_columns():
    """Quoted identifiers SELECT_COLS reads."""
    return {m.group(1) for m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_]*)"', SELECT_COLS)}


class EnrolmentDocumentsDdlTests(SimpleTestCase):
    def test_create_covers_every_selected_column(self):
        missing = sorted(_selected_columns() - _created_columns())
        self.assertEqual(
            missing,
            [],
            "documents.SELECT_COLS reads these columns but the CREATE TABLE "
            f"never creates them, so a fresh database 502s on every read: {missing}",
        )

    def test_signature_columns_are_present(self):
        # P0-1 specifically: these six existed only in apply_employer_signing
        # and apply_document_learner_signature.
        expected = {
            "Learner_signature",
            "Learner_signed_name",
            "Learner_signed_at",
            "Employer_signature",
            "Employer_signed_name",
            "Employer_signed_at",
        }
        self.assertTrue(
            expected <= _created_columns(),
            f"missing from CREATE: {sorted(expected - _created_columns())}",
        )

    def test_updated_at_is_present(self):
        # 6.3: replace_document_file mutates Blob_name/Doc_path/Size_bytes in
        # place, which Generated_at alone cannot distinguish.
        self.assertIn("Updated_at", _created_columns())

    def test_patch_columns_match_the_create(self):
        # An older database is brought up to date only by _PATCH_COLUMNS, so
        # every column added after first release has to appear in both.
        patched = {c for c, _ in _PATCH_COLUMNS}
        missing = sorted(patched - _created_columns())
        self.assertEqual(
            missing,
            [],
            f"_PATCH_COLUMNS adds columns the CREATE omits: {missing}",
        )

    # "Signed" is the original summary flag, present since the table's first
    # release, so no already-deployed database is missing it. The per-party
    # columns below are the ones that were bolted on afterwards.
    _ORIGINAL_COLUMNS = frozenset({"Signed"})

    def test_patch_covers_every_per_party_signature_column(self):
        # The reverse direction: a signature column present only in the CREATE
        # would leave every already-deployed database broken.
        patched = {c for c, _ in _PATCH_COLUMNS}
        per_party = {
            c
            for c in _selected_columns()
            if ("signature" in c.lower() or "signed" in c.lower())
            and c not in self._ORIGINAL_COLUMNS
        }
        missing = sorted(per_party - patched)
        self.assertEqual(
            missing,
            [],
            "These are read by SELECT_COLS and created for new databases, but "
            f"nothing patches an existing one: {missing}",
        )

    def test_patches_are_idempotent(self):
        self.assertIn("add column if not exists", _SOURCE)
        self.assertIn("create table if not exists", _SOURCE)
