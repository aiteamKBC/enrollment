from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0008_suitability_rpl_reports')]
    operations = [
        migrations.CreateModel(
            name='SuitabilityCvExtraction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('learner_kind', models.CharField(max_length=20)), ('learner_id', models.BigIntegerField()),
                ('source_document_id', models.CharField(max_length=200)), ('source_container', models.CharField(max_length=100)),
                ('source_blob_name', models.TextField()), ('source_blob_hash', models.CharField(max_length=64)),
                ('source_filename', models.CharField(blank=True, max_length=255)), ('source_content_type', models.CharField(blank=True, max_length=160)),
                ('extractor_version', models.CharField(default='cv-text-v1', max_length=40)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('ready', 'Ready'), ('unsupported', 'Unsupported'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('extracted_text', models.TextField(blank=True)), ('error_code', models.CharField(blank=True, max_length=80)),
                ('extracted_at', models.DateTimeField(blank=True, null=True)), ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ], options={'db_table': 'suitability_cv_extractions', 'indexes': [models.Index(fields=['learner_kind', 'learner_id', 'source_document_id'], name='suit_cv_learner_doc_idx')], 'constraints': [models.UniqueConstraint(fields=('learner_kind', 'learner_id', 'source_document_id', 'source_blob_hash'), name='unique_suitability_cv_source')]},
        ),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_extraction_acknowledged', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_extraction_acknowledged_by', field=models.BigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_extraction_acknowledged_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_extraction_acknowledgement_reason', field=models.TextField(blank=True)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_source_document_id', field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_source_container', field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_source_blob_name', field=models.TextField(blank=True)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_source_blob_hash', field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name='suitabilityrplreport', name='cv_extractor_version', field=models.CharField(blank=True, max_length=40)),
    ]
