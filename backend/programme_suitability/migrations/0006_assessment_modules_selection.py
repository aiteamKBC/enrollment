from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0005_modules_configuration')]

    operations = [
        migrations.AddField(model_name='assessment', name='modules_selection',
                            field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='assessment', name='modules_revision',
                            field=models.PositiveIntegerField(default=0)),
    ]
