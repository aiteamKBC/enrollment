from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0004_assessment_alternative_selection_reason')]
    operations = [
        migrations.AddField(model_name='assessment', name='modules_configuration', field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='assessment', name='modules_configuration_version', field=models.BigIntegerField(blank=True, null=True)),
        migrations.CreateModel(name='ModuleConfigurationDraft', fields=[
            ('actor_key', models.CharField(max_length=200, primary_key=True, serialize=False)),
            ('base_version', models.BigIntegerField(blank=True, null=True)),
            ('configuration', models.JSONField()),
            ('updated_at', models.DateTimeField(auto_now=True)),
        ], options={'db_table': 'programme_modules_admin_drafts'}),
    ]
