from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('programme_suitability', '0009_suitability_cv_extractions'),
    ]

    operations = [
        migrations.CreateModel(
            name='SuitabilityRplDocumentSignature',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('issued_document_id', models.UUIDField()),
                ('document_key', models.CharField(default='suitability-rpl-report', max_length=64)),
                ('learner_kind', models.CharField(max_length=20)),
                ('learner_id', models.BigIntegerField()),
                ('report_id', models.BigIntegerField()),
                ('report_version', models.PositiveIntegerField()),
                ('report_revision', models.PositiveIntegerField()),
                ('pdf_sha256', models.CharField(max_length=64)),
                ('signer_role', models.CharField(choices=[('admin', 'Admin / assessor'), ('learner', 'Learner'), ('employer', 'Employer / line manager')], max_length=20)),
                ('login_account_id', models.BigIntegerField()),
                ('signer_subject_type', models.CharField(max_length=20)),
                ('signer_subject_id', models.BigIntegerField()),
                ('signer_name', models.CharField(max_length=200)),
                ('signature_method', models.CharField(default='drawn', max_length=32)),
                ('signature', models.TextField()),
                ('consent_version', models.CharField(default='suitability-rpl-sign-v1', max_length=40)),
                ('consent_text', models.TextField()),
                ('consented_at', models.DateTimeField()),
                ('signed_at', models.DateTimeField()),
                ('ip_address', models.TextField(blank=True)),
                ('user_agent', models.TextField(blank=True)),
                ('audit_metadata', models.JSONField(default=dict)),
            ],
            options={'db_table': 'suitability_rpl_document_signatures'},
        ),
        migrations.AddConstraint(
            model_name='suitabilityrpldocumentsignature',
            constraint=models.UniqueConstraint(fields=('issued_document_id', 'signer_role'), name='unique_suit_rpl_issue_signature_role'),
        ),
        migrations.AddIndex(
            model_name='suitabilityrpldocumentsignature',
            index=models.Index(fields=('learner_kind', 'learner_id', 'issued_document_id'), name='suit_rpl_sig_issue_idx'),
        ),
    ]
