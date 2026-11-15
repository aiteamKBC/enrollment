from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0010_suitability_rpl_document_signatures')]

    operations = [
        migrations.AddField(
            model_name='suitabilityrplreport', name='parent_report',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='amended_versions', to='programme_suitability.suitabilityrplreport'),
        ),
        migrations.AddField(
            model_name='suitabilityrplreport', name='amendment_reason',
            field=models.TextField(blank=True),
        ),
    ]
