import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0018_catalog_delivery_end_date')]

    operations = [
        migrations.AddField(
            model_name='catalogksb', name='kind',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='catalogksb', name='standard',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='catalogksb', name='standard_version',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.CreateModel(
            name='CatalogSourceLink',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(max_length=50)),
                ('source_project_id', models.CharField(max_length=100)),
                ('source_branch_id', models.CharField(max_length=100)),
                ('source_entity_type', models.CharField(max_length=40)),
                ('source_entity_id', models.CharField(max_length=200)),
                ('local_entity_type', models.CharField(max_length=40)),
                ('local_record_id', models.UUIDField()),
                ('accepted_fingerprint', models.CharField(max_length=64)),
                ('accepted_state', models.JSONField(default=dict)),
                ('source_revision', models.CharField(blank=True, max_length=150)),
                ('local_revision', models.PositiveBigIntegerField(default=1)),
                ('last_synced_at', models.DateTimeField()),
                ('last_synced_by', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ('source_entity_type', 'source_entity_id', 'local_entity_type'),
                'indexes': [models.Index(
                    fields=['source_system', 'source_project_id', 'source_branch_id', 'source_entity_type'],
                    name='catalog_source_scope_idx',
                )],
                'constraints': [
                    models.UniqueConstraint(
                        fields=(
                            'source_system', 'source_project_id', 'source_branch_id',
                            'source_entity_type', 'source_entity_id', 'local_entity_type',
                        ), name='catalog_source_link_source_identity',
                    ),
                    models.UniqueConstraint(
                        fields=('local_entity_type', 'local_record_id'),
                        name='catalog_source_link_local_identity',
                    ),
                ],
            },
        ),
    ]
