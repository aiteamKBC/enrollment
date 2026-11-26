from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0019_catalog_lms_source_links')]

    operations = [
        migrations.CreateModel(
            name='SkillsRadarScreeningSetting',
            fields=[
                ('key', models.CharField(max_length=32, primary_key=True, serialize=False)),
                ('threshold_percent', models.PositiveSmallIntegerField(default=50)),
                ('revision', models.PositiveBigIntegerField(default=1)),
                ('history', models.JSONField(default=list)),
                ('updated_by', models.CharField(blank=True, max_length=200)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'programme_skills_radar_screening_settings'},
        ),
    ]
