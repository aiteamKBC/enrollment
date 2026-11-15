from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0007_preserve_legacy_module_choices')]

    operations = [
        migrations.CreateModel(
            name='SuitabilityRplReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('learner_kind', models.CharField(max_length=20)),
                ('learner_id', models.BigIntegerField()),
                ('context_key', models.CharField(max_length=64)),
                ('programme_id', models.CharField(blank=True, max_length=100)),
                ('programme_name', models.TextField(blank=True)),
                ('enrolment_reference', models.CharField(blank=True, max_length=200)),
                ('practical_start_date', models.CharField(blank=True, max_length=32)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('finalised', 'Finalised')], default='draft', max_length=20)),
                ('report_reference', models.CharField(max_length=100, unique=True)),
                ('report_version', models.PositiveIntegerField(default=1)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('relevant_prior_learning', models.BooleanField(blank=True, null=True)),
                ('reduction_justified', models.BooleanField(blank=True, null=True)),
                ('assessor_evidence_review', models.TextField(blank=True)),
                ('no_reduction_reason', models.TextField(blank=True)),
                ('suitability_outcome', models.CharField(choices=[('not_assessed', 'Not assessed'), ('suitable', 'Suitable'), ('further_assessment', 'Further assessment required')], default='not_assessed', max_length=32)),
                ('final_summary', models.TextField(blank=True)),
                ('suitability_justification', models.TextField(blank=True)),
                ('suitability_ai_metadata', models.JSONField(blank=True, default=dict)),
                ('suitability_reviewed_by', models.BigIntegerField(blank=True, null=True)),
                ('suitability_reviewed_by_name', models.CharField(blank=True, max_length=200)),
                ('suitability_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('rpl_justification', models.TextField(blank=True)),
                ('rpl_ai_metadata', models.JSONField(blank=True, default=dict)),
                ('rpl_reviewed_by', models.BigIntegerField(blank=True, null=True)),
                ('rpl_reviewed_by_name', models.CharField(blank=True, max_length=200)),
                ('rpl_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('source_snapshot', models.JSONField(default=dict)),
                ('source_versions', models.JSONField(default=dict)),
                ('source_snapshot_hash', models.CharField(max_length=64)),
                ('created_by', models.BigIntegerField(blank=True, null=True)),
                ('updated_by', models.BigIntegerField(blank=True, null=True)),
                ('finalised_by', models.BigIntegerField(blank=True, null=True)),
                ('finalised_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'suitability_rpl_reports'},
        ),
        migrations.CreateModel(
            name='SuitabilityRplReduction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('module_id', models.CharField(max_length=200)),
                ('module_title', models.TextField()),
                ('planned_hours', models.DecimalField(decimal_places=2, max_digits=9)),
                ('reduced_hours', models.DecimalField(decimal_places=2, max_digits=9)),
                ('ksb_codes', models.JSONField(blank=True, default=list)),
                ('evidence', models.TextField()),
                ('assessor_comments', models.TextField(blank=True)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reductions', to='programme_suitability.suitabilityrplreport')),
            ],
            options={'db_table': 'suitability_rpl_reductions', 'ordering': ('display_order', 'id')},
        ),
        migrations.AddConstraint(
            model_name='suitabilityrplreport',
            constraint=models.UniqueConstraint(condition=Q(status='draft'), fields=('learner_kind', 'learner_id', 'context_key'), name='unique_draft_suitability_rpl_context'),
        ),
        migrations.AddIndex(
            model_name='suitabilityrplreport',
            index=models.Index(fields=['learner_kind', 'learner_id', 'context_key'], name='suit_rpl_context_idx'),
        ),
        migrations.AddConstraint(
            model_name='suitabilityrplreduction',
            constraint=models.UniqueConstraint(fields=('report', 'module_id'), name='unique_suitability_rpl_module'),
        ),
    ]
