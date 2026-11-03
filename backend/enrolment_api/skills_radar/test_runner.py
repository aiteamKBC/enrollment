from django.db import connection
from programme_suitability.test_runner import SuitabilityRunner


class RadarRunner(SuitabilityRunner):
    def setup_databases(self, **kwargs):
        result = super().setup_databases(**kwargs)
        from enrolment_api.models import WizardSkillsRadar, WizardKsbAssessment
        with connection.schema_editor() as editor:
            editor.create_model(WizardSkillsRadar)
            editor.create_model(WizardKsbAssessment)
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE enrolment."Wizard_Form_Layouts" (id INTEGER PRIMARY KEY, "Layout" TEXT, "Created_at" TEXT, "Created_by" TEXT)')
        return result
