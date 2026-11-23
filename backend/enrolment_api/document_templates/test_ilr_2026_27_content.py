import json
from unittest.mock import patch

from django.test import SimpleTestCase

from .preview import sample
from .registry import seed, seeds, validate
from .service import field_values


class Ilr202627ContentTests(SimpleTestCase):
    def test_active_template_has_real_producers_and_approved_reference_content(self):
        template = seed("ilr")
        content = json.dumps(template)
        fields = seeds()["fields"]["ilr"]
        for binding in (
            "document.learnerDetails.learnerReferenceNumber",
            "document.learnerDetails.agreementIdentifier",
            "document.learnerDetails.dateStatusApplies",
            "document.learnerDetails.edrsErn",
            "document.answers.additionalInformation.residenceNotForFullTimeEducation",
            "document.answers.additionalInformation.ehcp",
            "document.answers.declarations.over50PercentEngland",
            "document.answers.declarations.wageRateBand",
            "document.answers.declarations.plrAccessAware",
        ):
            self.assertIn("{{" + binding + "}}", content)
            self.assertIn(binding, fields)
        self.assertIn("national-minimum-wage-rates", content)
        self.assertIn("By signing this declaration", content)
        self.assertNotIn("document.learnerDetails.isSmallEmployer", content)
        validate(template, publishing=True)

    def test_plr_explanation_precedes_existing_acknowledgement(self):
        ids = [block["id"] for block in seed("ilr")["blocks"]]
        self.assertLess(ids.index("ilr-plr-explanation"), ids.index("ilr-row-83"))

    def test_care_leaver_is_not_silently_reinterpreted(self):
        block = next(block for block in seed("ilr")["blocks"] if block["id"] == "ilr-row-66")
        self.assertEqual(block["label"], "Care leaver")

    def test_synthetic_preview_is_an_internally_consistent_employed_adult(self):
        learner, data = sample("ilr", ["ME_L4"])
        details = data["learnerDetails"]
        answers = data["answers"]
        self.assertEqual(details["dateOfBirth"], "1990-06-15")
        self.assertNotEqual(details["employmentStartDate"], details["dateStatusApplies"])
        self.assertEqual(details["employmentStatus"], "In paid employment")
        self.assertIs(details["selfEmployed"], False)
        self.assertEqual(answers["additional"], {"aged16to18": False, "aged19to24": False})
        self.assertIs(answers["declarations"]["knownByOtherName"], True)
        self.assertEqual(answers["additionalInformation"]["otherNames"], "SYNTHETIC Previous Name")
        with patch("enrolment_api.document_templates.learner_sources.document_dates", return_value=(None, None, "synthetic")):
            values = field_values("ilr", learner, data)
        for binding, expected in {
            "document.answers.additional.aged16to18": False,
            "document.answers.additional.aged19to24": False,
            "document.answers.media.consent": False,
            "document.answers.declarations.plrShared": True,
            "document.answers.declarations.dfeContact": True,
            "document.answers.declarations.epaoDetails": True,
            "document.answers.declarations.kbcHoldsCerts": True,
            "document.answers.declarations.infoAccurate": True,
        }.items():
            self.assertEqual(values[binding], expected)
