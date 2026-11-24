import django.core.validators
import django.db.models.deletion
import uuid
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('programme_suitability', '0012_group_capacity_preparation'),
    ]

    operations = [
        migrations.CreateModel(
            name='CatalogCohort',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='manual', editable=False, max_length=50)),
                ('external_id', models.CharField(blank=True, editable=False, max_length=200)),
                ('source_revision', models.CharField(blank=True, editable=False, max_length=150)),
                ('last_synced_at', models.DateTimeField(editable=False, null=True)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('start_date', models.DateField()),
                ('training_end_date', models.DateField()),
                ('epa_period', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(3650)])),
                ('epa_unit', models.CharField(choices=[('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months')], max_length=10)),
            ],
            options={
                'ordering': ('start_date', 'name', 'id'),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CatalogKsb',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='manual', editable=False, max_length=50)),
                ('external_id', models.CharField(blank=True, editable=False, max_length=200)),
                ('source_revision', models.CharField(blank=True, editable=False, max_length=150)),
                ('last_synced_at', models.DateTimeField(editable=False, null=True)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('description', models.TextField(max_length=5000)),
            ],
            options={
                'ordering': ('code',),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CatalogGroup',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='manual', editable=False, max_length=50)),
                ('external_id', models.CharField(blank=True, editable=False, max_length=200)),
                ('source_revision', models.CharField(blank=True, editable=False, max_length=150)),
                ('last_synced_at', models.DateTimeField(editable=False, null=True)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('coach_id', models.UUIDField(blank=True, null=True)),
                ('weekday', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(7)])),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('cohort', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='groups', to='programme_suitability.catalogcohort')),
            ],
            options={
                'ordering': ('name', 'id'),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CatalogModule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='manual', editable=False, max_length=50)),
                ('external_id', models.CharField(blank=True, editable=False, max_length=200)),
                ('source_revision', models.CharField(blank=True, editable=False, max_length=150)),
                ('last_synced_at', models.DateTimeField(editable=False, null=True)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('weeks', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(520)])),
                ('books', models.PositiveIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(10000)])),
                ('reviews', models.PositiveIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(10000)])),
                ('lectures', models.PositiveIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(10000)])),
                ('ksbs', models.ManyToManyField(blank=True, related_name='curriculum_modules', to='programme_suitability.catalogksb')),
            ],
            options={
                'ordering': ('name', 'id'),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CatalogDelivery',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='manual', editable=False, max_length=50)),
                ('external_id', models.CharField(blank=True, editable=False, max_length=200)),
                ('source_revision', models.CharField(blank=True, editable=False, max_length=150)),
                ('last_synced_at', models.DateTimeField(editable=False, null=True)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('start_date', models.DateField()),
                ('tutor_id', models.UUIDField(blank=True, null=True)),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='deliveries', to='programme_suitability.cataloggroup')),
                ('module', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='deliveries', to='programme_suitability.catalogmodule')),
            ],
            options={
                'ordering': ('start_date', 'id'),
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='CatalogProgramme',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_system', models.CharField(default='manual', editable=False, max_length=50)),
                ('external_id', models.CharField(blank=True, editable=False, max_length=200)),
                ('source_revision', models.CharField(blank=True, editable=False, max_length=150)),
                ('last_synced_at', models.DateTimeField(editable=False, null=True)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('code', models.CharField(max_length=80, unique=True)),
                ('level', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(8)])),
                ('description', models.TextField(blank=True, max_length=10000)),
                ('total_cost', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('epa_cost', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('epao', models.CharField(blank=True, max_length=200)),
                ('duration_months', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(120)])),
                ('funding_band_maximum', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('lecture_hours', models.DecimalField(decimal_places=2, default=Decimal('2.50'), max_digits=4, validators=[django.core.validators.MinValueValidator(Decimal('0.01')), django.core.validators.MaxValueValidator(Decimal('24'))])),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('archived', 'Archived')], default='draft', max_length=10)),
            ],
            options={
                'ordering': ('name', 'code'),
                'abstract': False,
                'constraints': [models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('source_system', 'external_id'), name='catalogprogramme_source_identity'), models.CheckConstraint(condition=models.Q(('epa_cost__gte', 0), ('total_cost__gte', models.F('epa_cost'))), name='catalog_programme_valid_cost')],
            },
        ),
        migrations.AddField(
            model_name='catalogmodule',
            name='programme',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='curriculum_modules', to='programme_suitability.catalogprogramme'),
        ),
        migrations.AddField(
            model_name='catalogksb',
            name='programme',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ksbs', to='programme_suitability.catalogprogramme'),
        ),
        migrations.AddField(
            model_name='catalogcohort',
            name='programme',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cohorts', to='programme_suitability.catalogprogramme'),
        ),
        migrations.CreateModel(
            name='CatalogWeeklyTopic',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('week', models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(520)])),
                ('content', models.TextField(max_length=5000)),
                ('archived_at', models.DateTimeField(editable=False, null=True)),
                ('module', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='weekly_topics', to='programme_suitability.catalogmodule')),
            ],
            options={
                'ordering': ('week',),
            },
        ),
        migrations.AddConstraint(
            model_name='cataloggroup',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('source_system', 'external_id'), name='cataloggroup_source_identity'),
        ),
        migrations.AddConstraint(
            model_name='cataloggroup',
            constraint=models.CheckConstraint(condition=models.Q(('end_time__gt', models.F('start_time'))), name='catalog_group_time_order'),
        ),
        migrations.AddConstraint(
            model_name='cataloggroup',
            constraint=models.CheckConstraint(condition=models.Q(('weekday__gte', 1), ('weekday__lte', 7)), name='catalog_group_weekday'),
        ),
        migrations.AddConstraint(
            model_name='catalogdelivery',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('source_system', 'external_id'), name='catalogdelivery_source_identity'),
        ),
        migrations.AddConstraint(
            model_name='catalogdelivery',
            constraint=models.UniqueConstraint(condition=models.Q(('archived_at__isnull', True)), fields=('group', 'module'), name='catalog_group_active_module'),
        ),
        migrations.AddConstraint(
            model_name='catalogmodule',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('source_system', 'external_id'), name='catalogmodule_source_identity'),
        ),
        migrations.AddConstraint(
            model_name='catalogksb',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('source_system', 'external_id'), name='catalogksb_source_identity'),
        ),
        migrations.AddConstraint(
            model_name='catalogksb',
            constraint=models.UniqueConstraint(fields=('programme', 'code'), name='catalog_programme_ksb_code'),
        ),
        migrations.AddConstraint(
            model_name='catalogcohort',
            constraint=models.UniqueConstraint(condition=models.Q(('external_id', ''), _negated=True), fields=('source_system', 'external_id'), name='catalogcohort_source_identity'),
        ),
        migrations.AddConstraint(
            model_name='catalogcohort',
            constraint=models.CheckConstraint(condition=models.Q(('training_end_date__gte', models.F('start_date'))), name='catalog_cohort_date_order'),
        ),
        migrations.AddConstraint(
            model_name='catalogweeklytopic',
            constraint=models.UniqueConstraint(fields=('module', 'week'), name='catalog_module_topic_week'),
        ),
    ]
