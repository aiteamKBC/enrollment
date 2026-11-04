"""Structured addresses: validation/layout/schema contracts, no DB or network."""
from copy import deepcopy
from pathlib import Path
import re

from django.test import SimpleTestCase

from enrolment_api import eligibility_form as form, eligibility_layout as layout
from enrolment_api.tests_eligibility_form import application, layout_with, TODAY


def legacy_layout(hidden=False):
    result = layout_with()
    for section in result["sections"]:
        section["items"] = [item for item in section["items"] if item["key"] not in {
            key for key, *_rest in form.ADDRESS_FIELDS
        }]
        legacy = "employerAddress" if section["slug"] == "employment" else "fullAddress" if section["slug"] == "residency" else None
        if legacy:
            anchor = "employerPostcode" if legacy == "employerAddress" else "postcode"
            position = next(i for i, item in enumerate(section["items"]) if item["key"] == anchor)
            section["items"].insert(position, {"key": legacy, "builtin": True, "hidden": hidden, "required": False})
    result["texts"] = {"employerAddress.label": "Company location", "fullAddress.hint": "Your declared home address"}
    return result


class StructuredAddressTests(SimpleTestCase):
    def test_four_visible_lines_use_existing_town_and_county_columns(self):
        payload = application(employerTownCity="Swansea", employerCounty="West Glamorgan, Wales",
                              residentialTownCity="Paris", residentialCounty="France")
        for key in form.LEGACY_ADDRESS_FIELDS:
            payload.pop(key, None)
        row, errors = form.clean_application(payload, today=TODAY)
        self.assertEqual(errors, {})
        self.assertEqual(row["employer_town_city"], "Swansea")
        self.assertEqual(row["employer_county"], "West Glamorgan, Wales")
        self.assertEqual(row["residential_town_city"], "Paris")
        self.assertEqual(row["residential_county"], "France")
        self.assertTrue(all(row[form.FIELD_BY_KEY[key][0]] is None for key in form.LEGACY_ADDRESS_FIELDS))

    def test_legacy_extra_details_cannot_replace_required_visible_line_three(self):
        _row, errors = form.clean_application(application(employerTownCity="", residentialTownCity="",
            employerAddressLine3="Forged town", residentialAddressLine3="Forged town"), today=TODAY)
        self.assertEqual(errors, {"employerTownCity": form.REQUIRED, "residentialTownCity": form.REQUIRED})

    def test_old_layout_cannot_require_removed_or_optional_address_inputs(self):
        raw = layout_with()
        for prefix in ("employer", "residential"):
            section = next(section for section in raw["sections"] if section["slug"] == ("employment" if prefix == "employer" else "residency"))
            section["items"].append({"key": f"{prefix}AddressLine3", "builtin": True, "hidden": False, "required": True})
            for item in section["items"]:
                if item["key"] in {f"{prefix}AddressLine2", f"{prefix}County"}:
                    item["required"] = True
        payload = application()
        for key in form.LEGACY_ADDRESS_FIELDS | {"employerCounty", "residentialCounty"}:
            payload.pop(key, None)
        row, errors = form.clean_application(payload, today=TODAY, layout=raw)
        self.assertEqual(errors, {})
        self.assertIsNone(row["employer_county"])
        normalized, columns = layout.normalize_layout(raw, None)
        self.assertEqual(columns, {})
        self.assertFalse(any(item["key"] in form.LEGACY_ADDRESS_FIELDS for section in normalized["sections"] for item in section["items"]))

    def test_required_parts_and_legacy_payload_cannot_substitute_for_them(self):
        for value in (None, "", "  "):
            with self.subTest(value=value):
                _row, errors = form.clean_application(application(
                    **dict.fromkeys(form.REQUIRED_ADDRESS_FIELDS, value),
                    fullAddress="Do not split this address", employerAddress="Forged full address",
                ), today=TODAY)
                self.assertEqual(errors, dict.fromkeys(form.REQUIRED_ADDRESS_FIELDS, form.REQUIRED))

    def test_optional_parts_trim_to_null_and_full_addresses_are_server_composed(self):
        row, errors = form.clean_application(application(
            employerAddressLine1="  2 Business Park  ", employerAddressLine2="Building B",
            employerAddressLine3="Floor 3", employerTownCity=" Swansea ", employerCounty="West Glamorgan",
            residentialAddressLine1="  10 Rue Exemple ", residentialAddressLine2=" ",
            residentialTownCity="Paris", residentialCounty="Île-de-France",
            fullAddress="FORGED", employerAddress="FORGED",
        ), today=TODAY)
        self.assertEqual(errors, {})
        self.assertEqual(row["employer_address"], "2 Business Park, Building B, Floor 3, Swansea, West Glamorgan")
        self.assertEqual(row["full_address"], "10 Rue Exemple, Paris, Île-de-France")
        self.assertIsNone(row["residential_address_line2"])
        self.assertIsNone(row["residential_address_line3"])

    def test_address_parts_have_independent_type_and_length_validation(self):
        for key, _column, _kind, _required, limit in form.ADDRESS_FIELDS:
            with self.subTest(field=key):
                _row, errors = form.clean_application(application(**{key: ["forged"]}), today=TODAY)
                self.assertEqual(errors, {key: "Enter text."})
                _row, errors = form.clean_application(application(**{key: "x" * (limit + 1)}), today=TODAY)
                self.assertEqual(errors, {key: f"Use {limit} characters or fewer."})

    def test_employer_parts_are_conditional_and_stale_answers_are_cleared(self):
        row, errors = form.clean_application(application(employmentStatus="Self employment"), today=TODAY)
        self.assertEqual(errors, {"employmentStatus": form.EMPLOYMENT_RESTRICTION_ERROR})
        self.assertIsNone(row["employer_address"])
        self.assertTrue(all(row[column] is None for key, column, *_rest in form.ADDRESS_FIELDS if key.startswith("employer")))

    def test_layout_cannot_make_visible_core_address_parts_optional(self):
        raw = layout_with(*[(key, {"required": False}) for key in form.REQUIRED_ADDRESS_FIELDS])
        _row, errors = form.clean_application(application(**dict.fromkeys(form.REQUIRED_ADDRESS_FIELDS, "")), layout=raw)
        self.assertEqual(errors, dict.fromkeys(form.REQUIRED_ADDRESS_FIELDS, form.REQUIRED))
        normalized, _ = layout.normalize_layout(raw, None)
        for section in normalized["sections"]:
            for item in section["items"]:
                if item["key"] in form.REQUIRED_ADDRESS_FIELDS:
                    self.assertIs(item["required"], True)

    def test_additive_sql_and_initializers_agree_without_postcode_or_country_duplicates(self):
        root = Path(__file__).resolve().parents[2]
        sql = (root / "database/eligibility_structured_addresses.sql").read_text(encoding="utf-8")
        additions = dict(re.findall(r"ADD COLUMN IF NOT EXISTS (\w+) (\w+)", sql))
        self.assertEqual(additions, dict.fromkeys(form.ADDRESS_COLUMNS, "text"))
        self.assertEqual(len(additions), 10)
        self.assertFalse(any("postcode" in column or "country" in column for column in additions))
        executable = " ".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        self.assertNotRegex(executable, r"(?i)\b(UPDATE|DELETE|INSERT|DROP|DEFAULT|NOT NULL)\b")
        schema = (root / "database/schema.sql").read_text(encoding="utf-8")
        table = re.search(r"CREATE TABLE enrolment\.eligibility_form \((.*?)\n\);", schema, re.S)[1]
        for column in form.ADDRESS_COLUMNS:
            self.assertIn(f"    {column} text,", table)
            self.assertIn((column, "text"), form.ADDED_COLUMNS)


class LegacyAddressLayoutTests(SimpleTestCase):
    def test_old_five_part_publications_cannot_restore_the_retired_extra_input(self):
        raw = layout_with()
        for section in raw["sections"]:
            if section["slug"] in {"employment", "residency"}:
                prefix = "employer" if section["slug"] == "employment" else "residential"
                section["items"].append({"key": f"{prefix}AddressLine3", "builtin": True, "hidden": False})
        before = deepcopy(raw)
        for upgraded in (layout.public_layout(raw), layout.normalize_layout(raw, raw)[0]):
            keys = [item["key"] for section in upgraded["sections"] for item in section["items"]]
            self.assertFalse(set(keys) & form.LEGACY_ADDRESS_FIELDS)
            for group in form.ADDRESS_INPUT_GROUPS.values():
                self.assertEqual(len(group), 4)
                self.assertTrue(set(group) <= set(keys))
        self.assertEqual(raw, before)

    def test_legacy_address_inputs_expand_at_their_old_position_without_rewriting_layouts(self):
        raw = legacy_layout()
        before = deepcopy(raw)
        for upgraded in (layout.public_layout(raw), layout.normalize_layout(raw, raw)[0]):
            self.assertEqual(len(upgraded["sections"]), 3)
            for section in upgraded["sections"]:
                keys = [item["key"] for item in section["items"]]
                for legacy, group in form.ADDRESS_INPUT_GROUPS.items():
                    self.assertNotIn(legacy, keys)
                    if group[0] in keys:
                        position = keys.index(group[0])
                        self.assertEqual(keys[position:position + 4], list(group))
                        self.assertEqual(keys[position + 4], "employerPostcode" if legacy == "employerAddress" else "postcode")
            self.assertEqual(upgraded["texts"]["employerAddressLine1.label"], "Company location")
            self.assertEqual(upgraded["texts"]["residentialAddressLine1.hint"], "Your declared home address")
        self.assertEqual(raw, before)

    def test_hidden_legacy_groups_stay_hidden_and_stale_answers_are_not_stored(self):
        raw = legacy_layout(hidden=True)
        upgraded = layout.public_layout(raw)
        keys = {key for key, *_rest in form.ADDRESS_FIELDS}
        self.assertTrue(all(item.get("hidden") for section in upgraded["sections"] for item in section["items"] if item["key"] in keys))
        row, errors = form.clean_application(application(), layout=raw)
        self.assertEqual(errors, {})
        self.assertIsNone(row["employer_address"])
        self.assertIsNone(row["full_address"])
        self.assertTrue(all(row[column] is None for column in form.ADDRESS_COLUMNS))

    def test_explicit_structured_edits_win_over_legacy_wording_and_are_not_duplicated(self):
        raw = legacy_layout()
        personal = raw["sections"][0]
        personal["items"].append({"key": "employerTownCity", "builtin": True, "hidden": False})
        raw["texts"]["employerAddressLine1.label"] = "Edited address line"
        upgraded = layout.structured_address_layout(raw)
        self.assertEqual(upgraded["sections"][0]["items"][-1]["key"], "employerTownCity")
        keys = [item["key"] for section in upgraded["sections"] for item in section["items"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(upgraded["texts"]["employerAddressLine1.label"], "Edited address line")
