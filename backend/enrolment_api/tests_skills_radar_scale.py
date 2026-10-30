"""The Skills Radar's 9-point scale: Champion (9) above Mastery (8), every
other level keeping its score, so ratings saved before Champion existed mean
what they always did — in the saved assessments and in both audit ledgers.

SimpleTestCase: the ORM writes are patched, so no query reaches a database.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from audit_api import learner_match_ledger_views as learner_ledger
from enrolment_api import wizard_steps
from manual_audit_api import match_ledger_views as manual_ledger


class SavedAssessmentTests(SimpleTestCase):
    def project(self, levels):
        draft = {"skillsRadar": {"assessments": {
            ksb: {"level": level, "evidenceFiles": [], "actionPlan": None} for ksb, level in levels.items()
        }}}
        with patch.object(wizard_steps.WizardSkillsRadar.objects, "update_or_create"), \
                patch.object(wizard_steps.WizardKsbAssessment.objects, "update_or_create") as upsert, \
                patch.object(wizard_steps.WizardKsbAssessment.objects, "filter", return_value=MagicMock()):
            wizard_steps.project_draft("apprenticeship", 41, draft)
        return {call.kwargs["ksb_id"]: call.kwargs["defaults"] for call in upsert.call_args_list}

    def test_champion_is_stored_with_a_score_of_9(self):
        saved = self.project({"k1": "champion", "k2": "Mastery", "k3": "never"})
        self.assertEqual((saved["k1"]["level"], saved["k1"]["score"]), ("champion", 9))
        # The existing levels keep their scores.
        self.assertEqual((saved["k2"]["level"], saved["k2"]["score"]), ("mastery", 8))
        self.assertEqual(saved["k3"]["score"], 1)

    def test_an_unknown_level_is_still_not_stored(self):
        saved = self.project({"k1": "grandmaster"})
        self.assertEqual((saved["k1"]["level"], saved["k1"]["score"]), (None, None))

    def test_the_scale_has_nine_points(self):
        current = {level: score for level, score in wizard_steps.LEVEL_SCORES.items() if level not in {"always", "often", "sometimes"}}
        self.assertEqual(sorted(current.values()), list(range(1, 10)))


class AuditLedgerTests(SimpleTestCase):
    def test_both_ledgers_score_champion_as_9_out_of_9(self):
        for ledger in (learner_ledger, manual_ledger):
            with self.subTest(ledger=ledger.__name__):
                self.assertEqual(ledger._skill_radar_level_score("Champion"), 9)
                self.assertEqual(ledger._skill_radar_level_score("mastery"), 8)
                self.assertEqual(ledger._skill_radar_level_score("never"), 1)
                [entry] = ledger._skill_radar_snapshot_entries([("Knowledge", "K1", "Marketing principles", "champion")])
                self.assertEqual((entry["knowledge"], entry["maximum"]), (9, 9))
