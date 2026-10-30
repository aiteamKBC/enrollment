"""Wizard builder: layout validation, storage assignment, DDL, publishing and
the projection of custom answers into their columns.

SimpleTestCase with a recording fake cursor: no query reaches a database.
"""
import json
import os
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from enrolment_api import wizard_layout as wl


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


def custom(label, type_="text", **extra):
    return {"key": extra.pop("key", ""), "builtin": False, "hidden": False, "label": label, "type": type_, **extra}


def step(slug, items, label=None, builtin=True, **extra):
    return {"slug": slug, "label": label or slug, "builtin": builtin, "hidden": False, "items": items, **extra}


class NormalizeLayoutTests(SimpleTestCase):
    def test_new_custom_field_gets_a_column_in_its_steps_table(self):
        layout, plan = wl.normalize_layout(
            {"steps": [step("cv-job", [{"key": "cv.experience", "builtin": True, "hidden": False},
                                       custom("Years in role", "number", required=True)])]},
            None,
        )
        item = layout["steps"][0]["items"][1]
        self.assertEqual(item["key"], "cf_years_in_role")
        self.assertEqual(item["table"], "Wizard_Cv_Job")
        self.assertEqual(item["column"], "Custom_years_in_role")
        self.assertTrue(item["required"])
        self.assertEqual(plan["Wizard_Cv_Job"]["columns"], {"Custom_years_in_role": "numeric"})
        # An existing table is never re-created, only extended.
        self.assertFalse(plan["Wizard_Cv_Job"]["create"])
        self.assertEqual(plan["Wizard_Cv_Job"]["index"], "wizard_cv_job_learner_uniq")

    def test_client_cannot_choose_storage(self):
        layout, _plan = wl.normalize_layout(
            {"steps": [step("personal-details", [custom("Hobby", table="Created_users", column="Password")])]},
            None,
        )
        item = layout["steps"][0]["items"][0]
        self.assertEqual((item["table"], item["column"]), ("Wizard_Personal_Details", "Custom_hobby"))

    def test_a_step_added_in_the_builder_gets_its_own_table(self):
        layout, plan = wl.normalize_layout(
            {"steps": [step("new", [custom("Laptop model")], label="Equipment", builtin=False)]},
            None,
        )
        new_step = layout["steps"][0]
        self.assertTrue(new_step["slug"].startswith("custom-equipment-"))
        self.assertEqual(new_step["table"], "Wizard_Custom_equipment")
        self.assertTrue(plan["Wizard_Custom_equipment"]["create"])

    def test_steps_without_a_learner_table_get_one_created_for_custom_fields(self):
        _layout, plan = wl.normalize_layout({"steps": [step("policies", [custom("Read the handbook?", "dropdown", options=["Yes", "No"])])]}, None)
        self.assertTrue(plan["Wizard_Policies"]["create"])
        self.assertEqual(plan["Wizard_Policies"]["columns"], {"Custom_read_the_handbook": "text"})

    def test_published_field_keeps_its_key_and_column_and_type(self):
        first, _ = wl.normalize_layout({"steps": [step("cv-job", [custom("Years in role", "number")])]}, None)
        renamed = json.loads(json.dumps(first))
        renamed["steps"][0]["items"][0]["label"] = "Years in current role"
        second, _ = wl.normalize_layout(renamed, first)
        item = second["steps"][0]["items"][0]
        self.assertEqual((item["key"], item["column"]), ("cf_years_in_role", "Custom_years_in_role"))
        self.assertEqual(item["label"], "Years in current role")

        retyped = json.loads(json.dumps(first))
        retyped["steps"][0]["items"][0]["type"] = "text"
        with self.assertRaisesMessage(wl.LayoutError, "type cannot change"):
            wl.normalize_layout(retyped, first)

    def test_a_field_left_out_of_the_layout_is_kept_hidden_with_its_column(self):
        first, _ = wl.normalize_layout({"steps": [step("cv-job", [custom("Years in role", "number")])]}, None)
        second, plan = wl.normalize_layout({"steps": [step("cv-job", [])]}, first)
        kept = second["steps"][0]["items"]
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["hidden"])
        self.assertEqual(kept[0]["column"], "Custom_years_in_role")
        # Its column is still part of the plan, so nothing ever drops it.
        self.assertIn("Custom_years_in_role", plan["Wizard_Cv_Job"]["columns"])

    def test_same_label_twice_gets_distinct_columns(self):
        layout, _ = wl.normalize_layout({"steps": [step("cv-job", [custom("Notes"), custom("Notes")])]}, None)
        cols = [i["column"] for i in layout["steps"][0]["items"]]
        self.assertEqual(cols, ["Custom_notes", "Custom_notes_2"])

    def test_dropdown_needs_options(self):
        with self.assertRaisesMessage(wl.LayoutError, "at least one option"):
            wl.normalize_layout({"steps": [step("cv-job", [custom("Shift", "dropdown", options=["  "])])]}, None)

    def test_unknown_type_is_refused(self):
        with self.assertRaisesMessage(wl.LayoutError, "needs a type"):
            wl.normalize_layout({"steps": [step("cv-job", [custom("Thing", "date")])]}, None)

    def test_condition_on_a_custom_dropdown_must_use_its_options(self):
        trigger = custom("Shift", "dropdown", key="cf_shift", options=["Day", "Night"])
        first, _ = wl.normalize_layout({"steps": [step("cv-job", [trigger])]}, None)
        follow = custom("Night allowance", condition={"field": "cf_shift", "values": ["Weekend"]})
        with self.assertRaisesMessage(wl.LayoutError, "does not offer: Weekend"):
            wl.normalize_layout({"steps": [step("cv-job", [first["steps"][0]["items"][0], follow])]}, first)

        follow["condition"]["values"] = ["Night"]
        layout, _ = wl.normalize_layout({"steps": [step("cv-job", [first["steps"][0]["items"][0], follow])]}, first)
        self.assertEqual(layout["steps"][0]["items"][1]["condition"], {"field": "cf_shift", "values": ["Night"]})

    def test_condition_on_a_field_added_in_the_same_publish_follows_its_new_key(self):
        layout, _ = wl.normalize_layout({"steps": [step("cv-job", [
            custom("Shift", "dropdown", key="new_1", options=["Day", "Night"]),
            custom("Night allowance", condition={"field": "new_1", "values": ["Night"]}),
        ])]}, None)
        items = layout["steps"][0]["items"]
        self.assertEqual(items[0]["key"], "cf_shift")
        self.assertEqual(items[1]["condition"]["field"], "cf_shift")

    def test_a_published_custom_step_keeps_its_slug_and_table(self):
        first, _ = wl.normalize_layout({"steps": [step("tmp-1", [custom("Laptop")], label="Equipment", builtin=False)]}, None)
        renamed = json.loads(json.dumps(first))
        renamed["steps"][0]["label"] = "Kit"
        second, _ = wl.normalize_layout(renamed, first)
        self.assertEqual(second["steps"][0]["slug"], first["steps"][0]["slug"])
        self.assertEqual(second["steps"][0]["table"], "Wizard_Custom_equipment")

    def test_condition_must_point_at_a_dropdown_in_the_wizard(self):
        with self.assertRaisesMessage(wl.LayoutError, "not in the wizard"):
            wl.normalize_layout({"steps": [step("cv-job", [custom("X", condition={"field": "cf_missing", "values": ["a"]})])]}, None)
        first, _ = wl.normalize_layout({"steps": [step("cv-job", [custom("Plain")])]}, None)
        with self.assertRaisesMessage(wl.LayoutError, "only depend on a dropdown"):
            wl.normalize_layout({"steps": [step("cv-job", [
                first["steps"][0]["items"][0], custom("Y", condition={"field": "cf_plain", "values": ["a"]}),
            ])]}, first)

    def test_condition_on_a_builtin_dropdown_needs_its_answer_path(self):
        builtin = {"key": "ild.employmentStatus", "builtin": True, "hidden": False}
        with self.assertRaisesMessage(wl.LayoutError, "where its answer is"):
            wl.normalize_layout({"steps": [step("ilr-details", [builtin, custom(
                "Employer size", condition={"field": "ild.employmentStatus", "values": ["In paid employment"]})])]}, None)
        layout, _ = wl.normalize_layout({"steps": [step("ilr-details", [builtin, custom(
            "Employer size", condition={"field": "ild.employmentStatus", "values": ["In paid employment"],
                                        "path": "ilrDetails.employmentStatus"})])]}, None)
        self.assertEqual(layout["steps"][0]["items"][1]["condition"]["path"], "ilrDetails.employmentStatus")

    def test_builtin_items_keep_only_layout_settings(self):
        layout, plan = wl.normalize_layout({"steps": [step("personal-details", [
            {"key": "pd.address", "builtin": True, "hidden": True, "required": False, "table": "x", "label": "y"},
        ])]}, None)
        self.assertEqual(layout["steps"][0]["items"][0], {"key": "pd.address", "builtin": True, "hidden": True, "required": False})
        self.assertEqual(plan, {})

    def test_edited_wording_is_kept_and_blank_edits_dropped(self):
        layout, plan = wl.normalize_layout({
            "steps": [step("introduction", [])],
            "texts": {"block.introduction.body": "## Hello\n**Welcome**", "pd.address.label": "  "},
        }, None)
        self.assertEqual(layout["texts"], {"block.introduction.body": "## Hello\n**Welcome**"})
        self.assertEqual(plan, {})
        no_edits, _ = wl.normalize_layout({"steps": [step("introduction", [])], "texts": {}}, None)
        self.assertNotIn("texts", no_edits)

    def test_edited_wording_shape_is_checked(self):
        for texts, message in (
            (["x"], "must be an object"),
            ({"bad key!": "x"}, "invalid key"),
            ({"pd.address.label": 5}, "must be text"),
            ({"pd.address.label": "x" * (wl.MAX_TEXT_LENGTH + 1)}, "characters or fewer"),
        ):
            with self.assertRaisesMessage(wl.LayoutError, message):
                wl.normalize_layout({"steps": [step("introduction", [])], "texts": texts}, None)

    def test_duplicate_item_is_refused(self):
        item = {"key": "pd.address", "builtin": True, "hidden": False}
        with self.assertRaisesMessage(wl.LayoutError, "appears twice"):
            wl.normalize_layout({"steps": [step("personal-details", [item, dict(item)])]}, None)


class ApplyPlanTests(SimpleTestCase):
    def test_adds_columns_and_the_upsert_index_without_dropping_anything(self):
        cursor = FakeCursor()
        wl.apply_plan(cursor, {
            "Wizard_Cv_Job": {"index": "wizard_cv_job_learner_uniq", "create": False, "columns": {"Custom_years": "numeric"}},
            "Wizard_Custom_equipment": {"index": "wizard_custom_equipment_learner_uniq", "create": True, "columns": {"Custom_laptop": "text"}},
        })
        sql = [s for s, _ in cursor.executed]
        self.assertIn('ALTER TABLE enrolment."Wizard_Cv_Job" ADD COLUMN IF NOT EXISTS "Custom_years" numeric', sql)
        self.assertTrue(any(s.startswith('CREATE TABLE IF NOT EXISTS enrolment."Wizard_Custom_equipment"') for s in sql))
        self.assertFalse(any(s.startswith('CREATE TABLE IF NOT EXISTS enrolment."Wizard_Cv_Job"') for s in sql))
        self.assertTrue(all("DROP" not in s.upper() for s in sql))

    def test_refuses_an_unsafe_identifier(self):
        with self.assertRaises(wl.LayoutError):
            wl.apply_plan(FakeCursor(), {'Wizard_x"; DROP TABLE y; --': {"index": "i", "create": False, "columns": {}}})
        with self.assertRaises(wl.LayoutError):
            wl.apply_plan(FakeCursor(), {"Wizard_Cv_Job": {"index": "i", "create": False, "columns": {'Custom_a" text; --': "text"}}})


LAYOUT = {"steps": [
    step("ilr-details", [
        {"key": "ild.employmentStatus", "builtin": True, "hidden": False},
        {"key": "cf_employer_size", "builtin": False, "hidden": False, "label": "Employer size", "type": "number",
         "table": "Wizard_Ilr_Learner_Details", "column": "Custom_employer_size",
         "condition": {"field": "ild.employmentStatus", "values": ["In paid employment"], "path": "ilrDetails.employmentStatus"}},
    ]),
    step("cv-job", [
        {"key": "cf_shift", "builtin": False, "hidden": False, "label": "Shift", "type": "dropdown", "options": ["Day", "Night"],
         "table": "Wizard_Cv_Job", "column": "Custom_shift"},
        {"key": "cf_night_note", "builtin": False, "hidden": False, "label": "Night note", "type": "text",
         "table": "Wizard_Cv_Job", "column": "Custom_night_note", "condition": {"field": "cf_shift", "values": ["Night"]}},
        {"key": "cf_old", "builtin": False, "hidden": True, "label": "Old", "type": "text",
         "table": "Wizard_Cv_Job", "column": "Custom_old"},
        {"key": "cf_cert", "builtin": False, "hidden": False, "label": "Certificate", "type": "upload",
         "table": "Wizard_Cv_Job", "column": "Custom_cert"},
    ]),
]}


class ProjectCustomFieldsTests(SimpleTestCase):
    def _project(self, draft, answers=None):
        cursor = FakeCursor()
        with patch.object(wl, "connections", FakeConnections(cursor)):
            wl.project_custom_fields("apprenticeship", 41, draft, answers=answers, layout=LAYOUT)
        return cursor.executed

    def test_upserts_each_tables_answers(self):
        executed = self._project({
            "ilrDetails": {"employmentStatus": "In paid employment"},
            "custom": {"cf_employer_size": " 250 ", "cf_shift": "Night", "cf_night_note": "Weekends"},
        })
        by_table = {sql.split('enrolment."')[1].split('"')[0]: (sql, params) for sql, params in executed}
        sql, params = by_table["Wizard_Cv_Job"]
        self.assertIn('ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET', sql)
        self.assertEqual(params, ["apprenticeship", 41, "Night", "Weekends"])
        self.assertEqual(by_table["Wizard_Ilr_Learner_Details"][1], ["apprenticeship", 41, Decimal("250")])

    def test_answers_to_questions_not_shown_are_cleared(self):
        executed = self._project({
            "ilrDetails": {"employmentStatus": "Not in paid employment"},
            "custom": {"cf_employer_size": "250", "cf_shift": "Day", "cf_night_note": "stale"},
        })
        params = {sql.split('enrolment."')[1].split('"')[0]: p for sql, p in executed}
        self.assertEqual(params["Wizard_Ilr_Learner_Details"], ["apprenticeship", 41, None])
        self.assertEqual(params["Wizard_Cv_Job"], ["apprenticeship", 41, "Day", None])

    def test_removed_fields_uploads_and_absent_keys_are_left_alone(self):
        executed = self._project({"custom": {"cf_old": "new value", "cf_cert": [{"id": "x"}], "cf_unknown": "?"}})
        self.assertEqual(executed, [])

    def test_bad_number_and_unknown_dropdown_option_become_null(self):
        executed = self._project({
            "ilrDetails": {"employmentStatus": "In paid employment"},
            "custom": {"cf_employer_size": "lots", "cf_shift": "Evening"},
        })
        params = {sql.split('enrolment."')[1].split('"')[0]: p for sql, p in executed}
        self.assertEqual(params["Wizard_Ilr_Learner_Details"], ["apprenticeship", 41, None])
        self.assertEqual(params["Wizard_Cv_Job"], ["apprenticeship", 41, None])

    def test_no_custom_answers_means_no_query(self):
        with patch.object(wl, "current_layout") as current:
            wl.project_custom_fields("apprenticeship", 41, {"personalDetails": {}})
        current.assert_not_called()


class PublishViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        for p in (
            patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}),
            patch.object(wl.transaction, "atomic", lambda **_: nullcontext()),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _put(self, body, cursor):
        request = self.factory.put("/enrolment_api/wizard-layout/publish/", data=json.dumps(body), content_type="application/json")
        with patch.object(wl, "connections", FakeConnections(cursor)):
            return wl.publish_wizard_layout(request)

    def test_publishes_a_new_version_with_its_columns(self):
        now = datetime(2026, 10, 3, tzinfo=timezone.utc)
        # to_regclass (exists), latest row (none yet), INSERT ... RETURNING
        cursor = FakeCursor([("x",), None, (7, now)])
        res = self._put({"baseVersion": None, "layout": {"steps": [step("cv-job", [custom("Years in role", "number")])]}}, cursor)
        self.assertEqual(res.status_code, 200, res.content)
        data = json.loads(res.content)
        self.assertEqual(data["version"], 7)
        self.assertEqual(data["layout"]["steps"][0]["items"][0]["column"], "Custom_years_in_role")
        sql = [s for s, _ in cursor.executed]
        self.assertTrue(any("ADD COLUMN IF NOT EXISTS \"Custom_years_in_role\" numeric" in s for s in sql))
        self.assertTrue(any(s.startswith('INSERT INTO enrolment."Wizard_Form_Layouts"') for s in sql))

    def test_refuses_when_someone_else_published_first(self):
        cursor = FakeCursor([("x",), (5, {"steps": []}, None, "someone")])
        res = self._put({"baseVersion": 4, "layout": {"steps": [step("cv-job", [])]}}, cursor)
        self.assertEqual(res.status_code, 409)
        self.assertFalse(any(s.startswith("INSERT") or "ALTER" in s for s, _ in cursor.executed))

    def test_invalid_layout_is_a_400_with_the_reason(self):
        cursor = FakeCursor([("x",), None])
        res = self._put({"baseVersion": None, "layout": {"steps": [step("cv-job", [custom("", "text")])]}}, cursor)
        self.assertEqual(res.status_code, 400)
        self.assertIn("needs a label", json.loads(res.content)["error"])


class LayoutAccessTests(SimpleTestCase):
    """The layout is readable by learners; publishing it is not."""

    def setUp(self):
        self.factory = RequestFactory()
        p = patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"})
        p.start()
        self.addCleanup(p.stop)

    def _as_learner(self, request):
        request.login_account = SimpleNamespace(is_active=True, role="learner", subject_id=41, subject_type="learner")
        request.user = SimpleNamespace(is_authenticated=False)
        return request

    def test_learner_can_read_the_layout(self):
        cursor = FakeCursor([(None,)])  # no layout table yet
        with patch.object(wl, "connections", FakeConnections(cursor)):
            res = wl.wizard_layout(self._as_learner(self.factory.get("/enrolment_api/wizard-layout/")))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(json.loads(res.content)["layout"], None)

    def test_learner_cannot_publish(self):
        request = self._as_learner(self.factory.put("/enrolment_api/wizard-layout/publish/", data="{}", content_type="application/json"))
        with patch.object(wl, "connections") as conns:
            res = wl.publish_wizard_layout(request)
        self.assertEqual(res.status_code, 404)
        conns.__getitem__.assert_not_called()

    def test_anonymous_cannot_read(self):
        request = self.factory.get("/enrolment_api/wizard-layout/")
        request.user = SimpleNamespace(is_authenticated=False)
        self.assertEqual(wl.wizard_layout(request).status_code, 401)
