from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0021_skills_radar_hard_ksb_screening_count')]

    operations = [
        migrations.AddField(
            model_name='skillsradarscreeningsetting',
            name='minimum_qualifying_rating',
            field=models.PositiveSmallIntegerField(default=5),
        ),
        migrations.AddField(
            model_name='skillsradarscreeningsetting',
            name='maximum_qualifying_rating',
            field=models.PositiveSmallIntegerField(default=9),
        ),
    ]
