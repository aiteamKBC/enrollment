from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("programme_suitability", "0003_assessment_selection_confirmed_at")]

    operations = [
        migrations.AddField(
            model_name="assessment",
            name="alternative_selection_reason",
            field=models.TextField(max_length=1000, null=True, blank=True),
        ),
    ]
