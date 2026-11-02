from django.db import migrations


def preserve_legacy_choices(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('enrolment.\"Extended_ILR\"')")
        if cursor.fetchone()[0] is None:
            return
        # Read legacy JSON once; write only the feature's existing assessment.
        cursor.execute('''
            UPDATE programme_suitability_assessments AS assessment
            SET modules_selection = legacy."Wizard_draft"->'programmeModules'
            FROM enrolment."Extended_ILR" AS legacy
            WHERE assessment.learner_id = legacy."Learner_id"
              AND assessment.learner_kind = legacy."Learner_kind"
              AND assessment.modules_selection IS NULL
              AND jsonb_typeof(legacy."Wizard_draft"->'programmeModules') = 'object'
        ''')


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0006_assessment_modules_selection')]
    operations = [migrations.RunPython(preserve_legacy_choices, migrations.RunPython.noop)]
