import calendar
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q


class CatalogRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_system = models.CharField(max_length=50, default='manual', editable=False)
    external_id = models.CharField(max_length=200, blank=True, editable=False)
    source_revision = models.CharField(max_length=150, blank=True, editable=False)
    last_synced_at = models.DateTimeField(null=True, editable=False)
    archived_at = models.DateTimeField(null=True, editable=False)
    revision = models.PositiveBigIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=('source_system', 'external_id'), condition=~Q(external_id=''),
                name='%(class)s_source_identity',
            ),
        ]


def amount(*, optional=False):
    return models.DecimalField(
        max_digits=12, decimal_places=2, null=optional, blank=optional,
        validators=[MinValueValidator(Decimal('0'))],
    )


def bounded_integer(minimum, maximum, **kwargs):
    return models.PositiveIntegerField(
        validators=[MinValueValidator(minimum), MaxValueValidator(maximum)], **kwargs,
    )


def calendar_month_anniversary(start, months):
    year, month_offset = divmod(start.year * 12 + start.month - 1 + months, 12)
    month = month_offset + 1
    return year, month, min(start.day, calendar.monthrange(year, month)[1])


def inclusive_month_end(start, months):
    year, month, day = calendar_month_anniversary(start, months)
    if day == 1:
        year, month_offset = divmod(year * 12 + month - 2, 12)
        month = month_offset + 1
        day = calendar.monthrange(year, month)[1] + 1
    return start.replace(year=year, month=month, day=day - 1)


def automatic_apprenticeship_end(start, training_end, training_months, epa_period, epa_unit, *, training_overridden):
    if not epa_period:
        return training_end
    if training_months is not None:
        if training_overridden:
            return inclusive_month_end(training_end + timedelta(days=1), epa_period)
        return inclusive_month_end(start, training_months + epa_period)
    if epa_unit == 'months':
        year, month, day = calendar_month_anniversary(training_end, epa_period)
        return training_end.replace(year=year, month=month, day=day)
    return training_end + timedelta(days=epa_period * (7 if epa_unit == 'weeks' else 1))


class CatalogProgramme(CatalogRecord):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        ACTIVE = 'active', 'Active'
        ARCHIVED = 'archived', 'Archived'

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=80, unique=True)
    level = bounded_integer(1, 8)
    description = models.TextField(blank=True, max_length=10000)
    total_cost = amount()
    epa_cost = amount()
    epao = models.CharField(max_length=200, blank=True)
    # Retain historical programme values. A new programme has no duration.
    duration_months = bounded_integer(1, 120, null=True, blank=True)
    funding_band_maximum = amount(optional=True)
    lecture_hours = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('2.50'),
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('24'))],
    )
    status = models.CharField(max_length=10, choices=Status, default=Status.DRAFT)

    class Meta(CatalogRecord.Meta):
        ordering = ('name', 'code')
        constraints = CatalogRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=Q(total_cost__gte=F('epa_cost'), epa_cost__gte=0),
                name='catalog_programme_valid_cost',
            ),
        ]


class CatalogCohort(CatalogRecord):
    class EpaUnit(models.TextChoices):
        DAYS = 'days', 'Days'
        WEEKS = 'weeks', 'Weeks'
        MONTHS = 'months', 'Months'

    programme = models.ForeignKey(CatalogProgramme, related_name='cohorts', on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    start_date = models.DateField()
    training_end_date = models.DateField()
    training_end_override = models.DateField(null=True, blank=True)
    apprenticeship_end_override = models.DateField(null=True, blank=True)
    training_duration_months = bounded_integer(1, 120, null=True, blank=True, editable=False)
    epa_period = bounded_integer(0, 3650)
    epa_unit = models.CharField(max_length=10, choices=EpaUnit)

    @property
    def effective_training_end_date(self):
        return self.training_end_override or self.training_end_date

    @property
    def duration_days(self):
        return (self.effective_training_end_date - self.start_date).days + 1

    @property
    def apprenticeship_end_date(self):
        return self.apprenticeship_end_override or automatic_apprenticeship_end(
            self.start_date, self.effective_training_end_date, self.training_duration_months,
            self.epa_period, self.epa_unit, training_overridden=self.training_end_override is not None,
        )

    @property
    def apprenticeship_duration_months(self):
        return self.training_duration_months + self.epa_period if self.training_duration_months is not None else None

    class Meta(CatalogRecord.Meta):
        ordering = ('start_date', 'name', 'id')
        constraints = CatalogRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=Q(training_end_date__gte=F('start_date')), name='catalog_cohort_date_order',
            ),
        ]


class CatalogGroup(CatalogRecord):
    cohort = models.ForeignKey(CatalogCohort, related_name='groups', on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    coach_id = models.UUIDField(null=True, blank=True)
    weekday = bounded_integer(1, 7)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta(CatalogRecord.Meta):
        ordering = ('name', 'id')
        constraints = CatalogRecord.Meta.constraints + [
            models.CheckConstraint(condition=Q(end_time__gt=F('start_time')), name='catalog_group_time_order'),
            models.CheckConstraint(condition=Q(weekday__gte=1, weekday__lte=7), name='catalog_group_weekday'),
        ]


class CatalogKsb(CatalogRecord):
    programme = models.ForeignKey(CatalogProgramme, related_name='ksbs', on_delete=models.PROTECT)
    code = models.CharField(max_length=40)
    description = models.TextField(max_length=5000)

    class Meta(CatalogRecord.Meta):
        ordering = ('code',)
        constraints = CatalogRecord.Meta.constraints + [
            models.UniqueConstraint(fields=('programme', 'code'), name='catalog_programme_ksb_code'),
        ]


class CatalogModule(CatalogRecord):
    programme = models.ForeignKey(CatalogProgramme, related_name='curriculum_modules', on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    weeks = bounded_integer(1, 520)
    books = bounded_integer(0, 10000, null=True, blank=True)
    reviews = bounded_integer(0, 10000, null=True, blank=True)
    lectures = bounded_integer(0, 10000, null=True, blank=True)
    ksbs = models.ManyToManyField(CatalogKsb, blank=True, related_name='curriculum_modules')

    class Meta(CatalogRecord.Meta):
        ordering = ('name', 'id')


class CatalogWeeklyTopic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(CatalogModule, related_name='weekly_topics', on_delete=models.PROTECT)
    week = bounded_integer(1, 520)
    content = models.TextField(max_length=5000)
    archived_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        ordering = ('week',)
        constraints = [models.UniqueConstraint(fields=('module', 'week'), name='catalog_module_topic_week')]


class CatalogModuleRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    module = models.ForeignKey(CatalogModule, related_name='revisions', on_delete=models.PROTECT)
    number = models.PositiveBigIntegerField()
    name = models.CharField(max_length=200)
    weeks = bounded_integer(1, 520)
    books = bounded_integer(0, 10000, null=True, blank=True)
    reviews = bounded_integer(0, 10000, null=True, blank=True)
    lectures = bounded_integer(0, 10000, null=True, blank=True)
    ksbs = models.JSONField(default=list)
    weekly_topics = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-number',)
        constraints = [models.UniqueConstraint(fields=('module', 'number'), name='catalog_module_revision_number')]


class CatalogDelivery(CatalogRecord):
    group = models.ForeignKey(CatalogGroup, related_name='deliveries', on_delete=models.PROTECT)
    module = models.ForeignKey(CatalogModule, related_name='deliveries', on_delete=models.PROTECT)
    module_revision = models.ForeignKey(CatalogModuleRevision, related_name='deliveries', on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date_override = models.DateField(null=True, blank=True)
    tutor_id = models.UUIDField(null=True, blank=True)

    class Meta(CatalogRecord.Meta):
        ordering = ('start_date', 'id')
        constraints = CatalogRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=Q(end_date_override__isnull=True) | Q(end_date_override__gte=F('start_date')),
                name='catalog_delivery_date_order',
            ),
            models.UniqueConstraint(
                fields=('group', 'module'), condition=Q(archived_at__isnull=True),
                name='catalog_group_active_module',
            ),
        ]

    @property
    def end_date(self):
        return self.end_date_override or delivery_end(self.start_date, self.module_revision.weeks)


def delivery_end(start_date, weeks):
    return start_date + timedelta(days=weeks * 7 - 1)


def validate_delivery_dates(start, end, cohort_start, cohort_end, *, weeks, field=None):
    try:
        end = end if end is not None else delivery_end(start, weeks)
    except OverflowError as exc:
        raise ValidationError({field or 'end_date': 'The module end date exceeds the supported date range.'}) from exc
    if start > end:
        raise ValidationError({field or 'end_date': 'End date must be on or after the start date.'})
    if start < cohort_start or start > cohort_end:
        raise ValidationError({field or 'start_date': 'The scheduled module must fit within the cohort training dates.'})
    if end > cohort_end:
        raise ValidationError({field or 'end_date': 'The scheduled module must fit within the cohort training dates.'})
