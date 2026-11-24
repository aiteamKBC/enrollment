from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0015_cohort_training_duration')]

    operations = [
        migrations.AddField(
            model_name='catalogcohort',
            name='training_end_override',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='catalogcohort',
            name='apprenticeship_end_override',
            field=models.DateField(blank=True, null=True),
        ),
    ]
