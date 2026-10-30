"""Eligibility form builder: layout validation, column assignment, publishing,
and who may read or publish the layout.

SimpleTestCase with a recording fake cursor: no query reaches a database.
"""
import json
import os
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from enrolment_api import eligibility_form as ef
from enrolment_api import eligibility_layout as el
from enrolment_api.wizard_layout import LayoutError


class FakeCursor:
    """Records every statement; answers fetchone() from a queue."""

    def __init__(self, results=None):
        self.executed = []
        self.results = list(results or [])

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnections(dict):
    def __init__(self, cursor):
        super().__init__(enrolment=SimpleNamespace(cursor=lambda: cursor))


def standard_layout():
    """The standard form, as the builder sends it before any change."""
    return {
        "sections": [
            {"slug": slug, "title": title, "builtin": True, "hidden": False,
             "items": [{"key": key, "builtin": True, "hidden": False} for key in keys]}
            for slug, (title, keys) in el.BUILTIN_SECTIONS.items()
        ]
    }


def custom(label, type_="text", **extra):
    return {"key": extra.pop("key", "new_1"), "builtin": False, "hidden": False, "label": label, "type": type_, **extra}


def with_items(layout, slug, *items):
    for section in layout["sections"]:
        if section["slug"] == slug:
            section["items"].extend(items)
    return layout


def items_of(layout, slug):
    return next(s for s in layout["sections"] if s["slug"] == slug)["items"]


def item(layout, key):
    return next(i for s in layout["sections"] for i in s["items"] if i["key"] == key)


class RegistryTests(SimpleTestCase):
    def test_every_builtin_question_sits_in_exactly_one_section(self):
        keys = [key for _title, keys in el.BUILTIN_SECTIONS.values() for key in keys]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(ef.FIELD_BY_KEY) | el.NOTES)


class NormalizeLayoutTests(SimpleTestCase):
    def test_the_standard_form_needs_no_columns(self):
        layout, columns = el.normalize_layout(standard_layout(), None)
        self.assertEqual(columns, {})
        self.assertEqual([s["slug"] for s in layout["sections"]], list(el.BUILTIN_SECTIONS))
        self.assertEqual(layout["sections"][0]["title"], "Personal Details")

    def test_a_new_question_gets_a_key_and_column_from_the_server(self):
        raw = with_items(standard_layout(), "employment", custom("Years in role?", "number", key="new_x", column="first_name"))
        layout, columns = el.normalize_layout(raw, None)
        added = items_of(layout, "employment")[-1]
        self.assertEqual(added["key"], "cf_years_in_role")
        # The client's column is ignored: the server names it.
        self.assertEqual(added["column"], "custom_years_in_role")
        self.assertEqual(columns, {"custom_years_in_role": "numeric"})

    def test_each_type_maps_to_its_sql_type(self):
        raw = with_items(
            standard_layout(), "residency",
            custom("A", "text", key="new_a"), custom("B", "number", key="new_b"), custom("C", "date", key="new_c"),
            custom("D", "yesno", key="new_d"), custom("E", "dropdown", key="new_e", options=["x"]),
        )
        _layout, columns = el.normalize_layout(raw, None)
        self.assertEqual(columns, {
            "custom_a": "text", "custom_b": "numeric", "custom_c": "date", "custom_d": "boolean", "custom_e": "text",
        })

    def test_same_label_twice_gets_distinct_keys_and_columns(self):
        raw = with_items(standard_layout(), "residency", custom("Notes", key="new_a"), custom("Notes", key="new_b"))
        layout, columns = el.normalize_layout(raw, None)
        self.assertEqual([i["key"] for i in items_of(layout, "residency")[-2:]], ["cf_notes", "cf_notes_2"])
        self.assertEqual(set(columns), {"custom_notes", "custom_notes_2"})

    def test_a_published_question_keeps_its_key_column_and_type(self):
        first, _ = el.normalize_layout(with_items(standard_layout(), "residency", custom("Visa expiry", "date")), None)
        published = item(first, "cf_visa_expiry")
        again = with_items(standard_layout(), "residency", {**published, "label": "Visa expiry date"})
        layout, columns = el.normalize_layout(again, first)
        self.assertEqual(item(layout, "cf_visa_expiry")["column"], "custom_visa_expiry")
        self.assertEqual(item(layout, "cf_visa_expiry")["label"], "Visa expiry date")
        self.assertEqual(columns, {"custom_visa_expiry": "date"})
        with self.assertRaisesMessage(LayoutError, "its type cannot change"):
            el.normalize_layout(with_items(standard_layout(), "residency", {**published, "type": "text"}), first)

    def test_a_published_question_left_out_comes_back_hidden(self):
        first, _ = el.normalize_layout(with_items(standard_layout(), "residency", custom("Visa expiry", "date")), None)
        layout, columns = el.normalize_layout(standard_layout(), first)
        restored = item(layout, "cf_visa_expiry")
        self.assertTrue(restored["hidden"])
        self.assertEqual(restored["column"], "custom_visa_expiry")
        self.assertEqual(columns, {"custom_visa_expiry": "date"})

    def test_builtin_questions_and_sections_left_out_come_back_hidden(self):
        raw = standard_layout()
        raw["sections"] = [s for s in raw["sections"] if s["slug"] != "residency"]
        items_of(raw, "employment").pop()  # representativeEmail
        layout, _ = el.normalize_layout(raw, None)
        residency = next(s for s in layout["sections"] if s["slug"] == "residency")
        self.assertTrue(residency["hidden"])
        self.assertTrue(item(layout, "livesInUk")["hidden"])
        self.assertTrue(item(layout, "representativeEmail")["hidden"])

    def test_the_questions_that_identify_the_applicant_must_stay(self):
        raw = standard_layout()
        item(raw, "workEmail")["hidden"] = True
        with self.assertRaisesMessage(LayoutError, "'Work Email Address' identifies the applicant"):
            el.normalize_layout(raw, None)
        raw = standard_layout()
        raw["sections"][0]["hidden"] = True
        with self.assertRaisesMessage(LayoutError, "'First Name' identifies the applicant"):
            el.normalize_layout(raw, None)
        raw = standard_layout()
        item(raw, "surname")["required"] = False
        layout, _ = el.normalize_layout(raw, None)
        self.assertNotIn("required", item(layout, "surname"))

    def test_builtin_items_keep_only_layout_settings(self):
        raw = standard_layout()
        item(raw, "phoneNumber").update(required=True, label="Hacked", column="first_name", options=["x"])
        item(raw, "programme").update(options=["  Data Analyst Level 4 ", "", "Data Analyst Level 4", "Marketing Manager Level 6"])
        layout, _ = el.normalize_layout(raw, None)
        self.assertEqual(item(layout, "phoneNumber"), {"key": "phoneNumber", "builtin": True, "hidden": False, "required": True})
        # Trimmed, blanks and repeats dropped.
        self.assertEqual(item(layout, "programme")["options"], ["Data Analyst Level 4", "Marketing Manager Level 6"])

    def test_a_choice_list_cannot_be_emptied(self):
        raw = standard_layout()
        item(raw, "programme")["options"] = ["", " "]
        with self.assertRaisesMessage(LayoutError, "at least one option"):
            el.normalize_layout(raw, None)

    def test_unknown_builtin_key_and_duplicates_are_refused(self):
        raw = with_items(standard_layout(), "residency", {"key": "password", "builtin": True})
        with self.assertRaisesMessage(LayoutError, "does not exist"):
            el.normalize_layout(raw, None)
        raw = with_items(standard_layout(), "residency", {"key": "firstName", "builtin": True})
        with self.assertRaisesMessage(LayoutError, "appears twice"):
            el.normalize_layout(raw, None)

    def test_a_condition_names_a_question_with_set_answers_and_one_of_its_answers(self):
        ok = with_items(standard_layout(), "programme",
                        custom("Which programme?", key="new_a", condition={"field": "attendedFundedTraining", "values": ["Yes"]}),
                        custom("Why marketing?", key="new_b", condition={"field": "programme", "values": ["Marketing Manager Level 6"]}))
        layout, _ = el.normalize_layout(ok, None)
        self.assertEqual(item(layout, "cf_why_marketing")["condition"], {"field": "programme", "values": ["Marketing Manager Level 6"]})

        free_text = with_items(standard_layout(), "programme", custom("X", key="new_a", condition={"field": "employerName", "values": ["Acme"]}))
        with self.assertRaisesMessage(LayoutError, "can only depend on a question with set answers"):
            el.normalize_layout(free_text, None)

        unknown = with_items(standard_layout(), "programme", custom("X", key="new_a", condition={"field": "livesInUk", "values": ["Maybe"]}))
        with self.assertRaisesMessage(LayoutError, "no longer offers: Maybe"):
            el.normalize_layout(unknown, None)

    def test_removing_an_answer_a_condition_relies_on_is_refused(self):
        raw = with_items(standard_layout(), "programme",
                         custom("Why marketing?", key="new_b", condition={"field": "programme", "values": ["Marketing Manager Level 6"]}))
        item(raw, "programme")["options"] = ["Associate Project Manager Level 4"]
        with self.assertRaisesMessage(LayoutError, "no longer offers: Marketing Manager Level 6"):
            el.normalize_layout(raw, None)

    def test_a_condition_on_a_question_added_in_the_same_publish_follows_its_new_key(self):
        raw = with_items(standard_layout(), "residency",
                         custom("Visa type", "dropdown", key="new_a", options=["Skilled", "Other"]),
                         custom("Which other visa?", key="new_b", condition={"field": "new_a", "values": ["Other"]}))
        layout, _ = el.normalize_layout(raw, None)
        self.assertEqual(item(layout, "cf_which_other_visa")["condition"]["field"], "cf_visa_type")

    def test_questions_depending_on_each_other_in_a_loop_are_refused(self):
        raw = with_items(standard_layout(), "residency",
                         custom("A", "yesno", key="new_a", condition={"field": "new_b", "values": ["Yes"]}),
                         custom("B", "yesno", key="new_b", condition={"field": "new_a", "values": ["Yes"]}))
        with self.assertRaisesMessage(LayoutError, "loop"):
            el.normalize_layout(raw, None)

    def test_custom_sections_get_a_server_slug_and_keep_it(self):
        raw = standard_layout()
        raw["sections"].append({"slug": "new-section-1", "title": "Extra details", "builtin": False, "hidden": False,
                                "description": "Anything else", "items": [custom("Notes")]})
        layout, _ = el.normalize_layout(raw, None)
        extra = layout["sections"][-1]
        self.assertRegex(extra["slug"], r"^custom-extra-details-[0-9a-f]{4}$")
        self.assertEqual(extra["description"], "Anything else")
        again, _ = el.normalize_layout(layout, layout)
        self.assertEqual(again["sections"][-1]["slug"], extra["slug"])

    def test_a_section_needs_a_name(self):
        raw = standard_layout()
        raw["sections"].append({"slug": "new-1", "title": " ", "builtin": False, "items": []})
        with self.assertRaisesMessage(LayoutError, "A section name is required"):
            el.normalize_layout(raw, None)

    def test_edited_wording_is_kept_and_blank_edits_dropped(self):
        raw = standard_layout()
        raw["texts"] = {"firstName.label": "Given name", "firstName.hint": "  "}
        layout, _ = el.normalize_layout(raw, None)
        self.assertEqual(layout["texts"], {"firstName.label": "Given name"})


class TableTests(SimpleTestCase):
    def test_custom_columns_are_added_without_dropping_anything(self):
        cursor = FakeCursor()
        ef.add_custom_columns(cursor, {"custom_years": "numeric"})
        self.assertEqual(cursor.executed, [('ALTER TABLE enrolment.eligibility_form ADD COLUMN IF NOT EXISTS "custom_years" numeric', None)])

    def test_unsafe_custom_columns_are_refused(self):
        for columns in ({'custom_a" text; DROP TABLE x; --': "text"}, {"first_name": "text"}, {"custom_a": "text; DROP"}):
            with self.assertRaises(ValueError):
                ef.add_custom_columns(FakeCursor(), columns)


class PublishViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        for p in (
            patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}),
            patch.object(el.transaction, "atomic", lambda **_: nullcontext()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _put(self, body, cursor, **headers):
        headers.setdefault("HTTP_X_REQUESTED_WITH", "XMLHttpRequest")
        request = self.factory.put("/enrolment_api/eligibility-form-layout/publish/", data=json.dumps(body),
                                   content_type="application/json", **headers)
        with patch.object(el, "connections", FakeConnections(cursor)):
            return el.publish_eligibility_form_layout(request)

    def test_publishes_a_new_version_and_adds_its_columns(self):
        now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        # to_regclass (exists), latest row (none yet), INSERT ... RETURNING
        cursor = FakeCursor([("x",), None, (3, now)])
        layout = with_items(standard_layout(), "employment", custom("Years in role", "number"))
        res = self._put({"baseVersion": None, "layout": layout}, cursor)
        self.assertEqual(res.status_code, 200, res.content)
        data = json.loads(res.content)
        self.assertEqual(data["version"], 3)
        self.assertEqual(item(data["layout"], "cf_years_in_role")["column"], "custom_years_in_role")
        sql = [s for s, _ in cursor.executed]
        self.assertIn('ALTER TABLE enrolment.eligibility_form ADD COLUMN IF NOT EXISTS "custom_years_in_role" numeric', sql)
        # Brings the table up to date first, and never drops anything.
        self.assertTrue(any(s.startswith("CREATE TABLE IF NOT EXISTS enrolment.eligibility_form") for s in sql))
        self.assertIn("ALTER TABLE enrolment.eligibility_form ADD COLUMN IF NOT EXISTS layout_version bigint", sql)
        self.assertFalse(any("DROP COLUMN" in s or "DROP TABLE" in s for s in sql))
        self.assertTrue(sql[-1].startswith('INSERT INTO enrolment."Eligibility_Form_Layouts"'))

    def test_refuses_when_someone_else_published_first(self):
        cursor = FakeCursor([("x",), (5, standard_layout(), None, "someone")])
        res = self._put({"baseVersion": 4, "layout": standard_layout()}, cursor)
        self.assertEqual(res.status_code, 409)
        self.assertFalse(any(s.startswith("INSERT") or "ALTER" in s for s, _ in cursor.executed))

    def test_invalid_layout_is_a_400_with_the_reason(self):
        cursor = FakeCursor([("x",), None])
        raw = standard_layout()
        item(raw, "firstName")["hidden"] = True
        res = self._put({"baseVersion": None, "layout": raw}, cursor)
        self.assertEqual(res.status_code, 400)
        self.assertIn("identifies the applicant", json.loads(res.content)["error"])
        self.assertFalse(any(s.startswith("INSERT") or "ALTER" in s for s, _ in cursor.executed))

    def test_requires_the_csrf_header(self):
        cursor = FakeCursor()
        request = self.factory.put("/enrolment_api/eligibility-form-layout/publish/", data="{}", content_type="application/json")
        with patch.object(el, "connections", FakeConnections(cursor)):
            self.assertEqual(el.publish_eligibility_form_layout(request).status_code, 403)
        self.assertEqual(cursor.executed, [])


class LayoutAccessTests(SimpleTestCase):
    """Anyone may read the form in force without names; only enrolment staff see who published it, or publish."""

    def setUp(self):
        self.factory = RequestFactory()
        p = patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"})
        p.start()
        self.addCleanup(p.stop)

    def _as_learner(self, request):
        request.login_account = SimpleNamespace(is_active=True, role="learner", subject_id=41, subject_type="learner")
        request.user = SimpleNamespace(is_authenticated=False)
        return request

    def test_the_public_layout_carries_no_names_or_dates(self):
        cursor = FakeCursor([("x",), (6, standard_layout(), datetime(2026, 10, 15, tzinfo=timezone.utc), "Staff Member")])
        request = self.factory.get("/enrolment_api/public/eligibility-form-layout/")
        request.user = SimpleNamespace(is_authenticated=False)
        with patch.object(el, "connections", FakeConnections(cursor)):
            res = el.public_eligibility_form_layout(request)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(json.loads(res.content)), {"layout", "version"})
        self.assertNotIn("Staff Member", res.content.decode())

    def test_the_public_layout_is_null_until_something_is_published(self):
        cursor = FakeCursor([(None,)])  # no layout table yet
        with patch.object(el, "connections", FakeConnections(cursor)):
            res = el.public_eligibility_form_layout(self.factory.get("/enrolment_api/public/eligibility-form-layout/"))
        self.assertEqual(json.loads(res.content), {"layout": None, "version": None})

    def test_learners_cannot_read_the_builder_layout_or_publish(self):
        for view, request in (
            (el.eligibility_form_layout, self.factory.get("/enrolment_api/eligibility-form-layout/")),
            (el.publish_eligibility_form_layout, self.factory.put(
                "/enrolment_api/eligibility-form-layout/publish/", data="{}", content_type="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest")),
        ):
            with patch.object(el, "connections") as conns:
                res = view(self._as_learner(request))
            self.assertEqual(res.status_code, 404)
            conns.__getitem__.assert_not_called()

    def test_anonymous_cannot_read_the_builder_layout(self):
        request = self.factory.get("/enrolment_api/eligibility-form-layout/")
        request.user = SimpleNamespace(is_authenticated=False)
        self.assertEqual(el.eligibility_form_layout(request).status_code, 401)
