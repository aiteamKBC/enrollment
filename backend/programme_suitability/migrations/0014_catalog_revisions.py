import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
from django.db.models.deletion import PROTECT


def pin_existing_deliveries(apps, schema_editor):
    module_model = apps.get_model('programme_suitability', 'CatalogModule')
    revision_model = apps.get_model('programme_suitability', 'CatalogModuleRevision')
    delivery_model = apps.get_model('programme_suitability', 'CatalogDelivery')
    alias = schema_editor.connection.alias
    for module in module_model.objects.using(alias).prefetch_related('ksbs', 'weekly_topics').iterator(chunk_size=200):
        revision = revision_model.objects.using(alias).create(
            module=module, number=module.revision, name=module.name, weeks=module.weeks,
            books=module.books, reviews=module.reviews, lectures=module.lectures,
            ksbs=[{'id': str(ksb.pk), 'code': ksb.code, 'description': ksb.description}
                  for ksb in module.ksbs.all()],
            weekly_topics=[{'id': str(topic.pk), 'week': topic.week, 'content': topic.content}
                           for topic in module.weekly_topics.all() if not topic.archived_at],
        )
        delivery_model.objects.using(alias).filter(module_id=module.pk).update(module_revision_id=revision.pk)
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):
    dependencies = [('programme_suitability', '0013_programme_catalog')]

    operations = [
        migrations.AddField(
            model_name=model_name, name='revision',
            field=models.PositiveBigIntegerField(default=1, editable=False),
        )
        for model_name in ('catalogprogramme', 'catalogcohort', 'cataloggroup', 'catalogksb', 'catalogmodule', 'catalogdelivery')
    ] + [
        migrations.CreateModel(
            name='CatalogModuleRevision',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('number', models.PositiveBigIntegerField()),
                ('name', models.CharField(max_length=200)),
                ('weeks', models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(520)])),
                *[
                    (name, models.PositiveIntegerField(blank=True, null=True, validators=[MinValueValidator(0), MaxValueValidator(10000)]))
                    for name in ('books', 'reviews', 'lectures')
                ],
                ('ksbs', models.JSONField(default=list)),
                ('weekly_topics', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('module', models.ForeignKey(on_delete=PROTECT, related_name='revisions', to='programme_suitability.catalogmodule')),
            ],
            options={
                'ordering': ('-number',),
                'constraints': [models.UniqueConstraint(fields=('module', 'number'), name='catalog_module_revision_number')],
            },
        ),
        migrations.AddField(
            model_name='catalogdelivery', name='module_revision',
            field=models.ForeignKey(null=True, on_delete=PROTECT, related_name='deliveries', to='programme_suitability.catalogmodulerevision'),
        ),
        migrations.RunPython(pin_existing_deliveries, migrations.RunPython.noop, hints={'model_name': 'catalogdelivery'}),
        migrations.AlterField(
            model_name='catalogdelivery', name='module_revision',
            field=models.ForeignKey(on_delete=PROTECT, related_name='deliveries', to='programme_suitability.catalogmodulerevision'),
        ),
    ]
