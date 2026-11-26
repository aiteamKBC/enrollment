from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0020_skills_radar_screening_setting')]

    operations = [
        migrations.AddField(
            model_name='skillsradarscreeningsetting',
            name='minimum_high_rated_hard_ksbs',
            field=models.PositiveSmallIntegerField(default=2),
        ),
    ]
