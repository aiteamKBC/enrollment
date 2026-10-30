"""The signed-in person's saved signature: read and replaced for their own
account only, in the table their account type already uses.

SimpleTestCase with the database cursor mocked, so no query reaches a database.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from login import saved_signature as view

PNG = "data:image/png;base64,iVBORw0KGgo="
URL = "/login_api/me/signature/"


def _account(subject_type="learner", subject_id=41, role="learner"):
    return SimpleNamespace(subject_type=subject_type, subject_id=subject_id, role=role,
                           display_name="Test Person", is_active=True)


class SavedSignatureTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = self.cursor
        p = patch.object(view, "_conn", return_value=conn)
        p.start()
        self.addCleanup(p.stop)

    def _call(self, request, account):
        def authenticate(req):
            req.login_account = account
            return account
        with patch("login.permissions.authenticate_request", side_effect=authenticate):
            return view.my_signature(request)

    def _put(self, account, signature=PNG, xhr=True):
        headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if xhr else {}
        request = self.factory.put(URL, data=json.dumps({"signature": signature}), content_type="application/json", **headers)
        return self._call(request, account)

    def test_reads_the_callers_own_row_in_their_account_table(self):
        saved_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.cursor.fetchone.return_value = (PNG, saved_at)
        for subject_type, table, column in (
            ("learner", 'enrolment."Created_users"', '"Learner_signature"'),
            ("employer", 'enrolment."Employers"', '"Signature"'),
            ("staff", 'enrolment."Staff_users"', '"Saved_signature"'),
        ):
            with self.subTest(subject_type):
                response = self._call(self.factory.get(URL), _account(subject_type, 77))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.content), {"signature": PNG, "savedAt": saved_at.isoformat()})
                sql, params = self.cursor.execute.call_args.args
                self.assertIn(table, sql)
                self.assertIn(column, sql)
                self.assertEqual(params, [77])

    def test_no_saved_signature_reads_as_empty(self):
        self.cursor.fetchone.return_value = ("", None)
        response = self._call(self.factory.get(URL), _account())
        self.assertEqual(json.loads(response.content), {"signature": "", "savedAt": ""})

    def test_saving_writes_only_the_callers_own_row(self):
        self.cursor.fetchone.return_value = (datetime(2026, 9, 28, tzinfo=timezone.utc),)

        response = self._put(_account("staff", 12, role="staff"))

        self.assertEqual(response.status_code, 200, response.content)
        sql, params = self.cursor.execute.call_args.args
        self.assertTrue(sql.startswith('update enrolment."Staff_users" set "Saved_signature"'))
        self.assertEqual(params, [PNG, "Test Person", 12])

    def test_a_signature_that_is_not_a_bounded_png_is_refused(self):
        for bad in ("data:image/jpeg;base64,AAAA", "not an image", PNG + "A" * view.MAX_SIGNATURE_CHARS):
            with self.subTest(bad[:24]):
                self.assertEqual(self._put(_account(), bad).status_code, 400)
        self.cursor.execute.assert_not_called()

    def test_saving_requires_the_cross_site_header(self):
        self.assertEqual(self._put(_account(), xhr=False).status_code, 403)
        self.cursor.execute.assert_not_called()

    def test_unauthenticated_callers_are_refused(self):
        with patch("login.permissions.authenticate_request", return_value=None):
            response = view.my_signature(self.factory.get(URL))
        self.assertEqual(response.status_code, 401)
        self.cursor.execute.assert_not_called()
