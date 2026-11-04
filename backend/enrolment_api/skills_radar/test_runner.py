from django.db import connection
from programme_suitability.test_runner import SuitabilityRunner


class RadarRunner(SuitabilityRunner):
    def setup_databases(self, **kwargs):
        result = super().setup_databases(**kwargs)
        from enrolment_api.models import (
            WizardPersonalDetails, WizardIlrLearnerDetails, WizardSkillsRadar,
            WizardKsbAssessment, WizardPlr, WizardPlrRecord, WizardCvJob,
            WizardPolicyAck,
        )
        with connection.schema_editor() as editor:
            for model in (
                WizardPersonalDetails, WizardIlrLearnerDetails, WizardSkillsRadar,
                WizardKsbAssessment, WizardPlr, WizardPlrRecord, WizardCvJob,
                WizardPolicyAck,
            ):
                editor.create_model(model)
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE enrolment."Wizard_Form_Layouts" (id INTEGER PRIMARY KEY, "Layout" TEXT, "Created_at" TEXT, "Created_by" TEXT)')
        return result
