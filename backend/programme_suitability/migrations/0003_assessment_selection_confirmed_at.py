from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("programme_suitability", "0002_assessment_programme_selection_source")]

    operations = [
        migrations.AddField(
            model_name="assessment",
            name="selection_confirmed_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
