from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from learner_api.models import StaffUser
from login.identity import accesses_for_staff
from .catalog_models import (
    CatalogCohort, CatalogDelivery, CatalogGroup, CatalogModuleRevision,
    automatic_apprenticeship_end, inclusive_month_end, validate_delivery_dates,
)


def normalized_code(value, queryset, *, max_length, record_id=None):
    value = value.strip().upper()
    if not value or len(value) > max_length:
        raise ValidationError({'code': 'Enter a non-empty code within the maximum length.'})
    if queryset.filter(code=value).exclude(pk=record_id).exists():
        raise ValidationError({'code': 'This code is already in use.'})
    return value


def staff_members():
    return StaffUser.objects.alias(status_key=Lower(Trim('status')), name_key=Trim('username')).exclude(
        username__isnull=True,
    ).exclude(name_key='').filter(
        Q(status__isnull=True) | ~Q(status_key__in=('inactive', 'archived', 'disabled')),
    ).only(
        'uuid', 'username', 'email', 'access', 'access_extra',
    ).order_by(Lower('name_key'), 'uuid')


def validate_staff(value, role):
    if value is not None:
        staff = staff_members().filter(uuid=value).first()
        if not staff or role not in accesses_for_staff(staff):
            raise ValidationError({f'{role}_id': f'Choose an active staff member with {role.title()} access.'})
    return value


def validate_programme_cost(total, epa):
    if epa < 0 or epa > total:
        raise ValidationError({'epa_cost': 'EPA cost must be non-negative and cannot exceed the total cost.'})


def calculate_cohort_schedule(start, training_months, epa_period, *, cohort=None,
                              training_end_override=None, apprenticeship_end_override=None):
    legacy = cohort is not None and training_months is None
    if not legacy and (type(training_months) is not int or not 1 <= training_months <= 120):
        raise ValidationError({'training_duration_months': 'Enter a planned duration between 1 and 120 months.'})
    if not 0 <= epa_period <= 3650:
        raise ValidationError({'epa_period': 'Enter an EPA period between 0 and 3650 months.'})
    try:
        automatic_end = cohort.training_end_date if legacy else inclusive_month_end(start, training_months)
    except (ValueError, OverflowError) as exc:
        raise ValidationError({'start_date': 'The training schedule exceeds the supported date range.'}) from exc
    end = training_end_override or automatic_end
    if end < start or automatic_end < start:
        raise ValidationError({'training_end_override': 'Training end date must be on or after the cohort start date.'})
    epa_unit = cohort.epa_unit if legacy else CatalogCohort.EpaUnit.MONTHS
    try:
        apprenticeship_end = apprenticeship_end_override or automatic_apprenticeship_end(
            start, end, training_months, epa_period, epa_unit, training_overridden=training_end_override is not None,
        )
    except (ValueError, OverflowError) as exc:
        raise ValidationError({'epa_period': 'The automatic apprenticeship end date exceeds the supported date range.'}) from exc
    if apprenticeship_end < end:
        raise ValidationError({'apprenticeship_end_override': 'Apprenticeship end date must be on or after the training end date.'})
    if cohort is not None and (start != cohort.start_date or end != cohort.effective_training_end_date):
        deliveries = CatalogDelivery.objects.filter(
            group__cohort=cohort, group__archived_at__isnull=True, archived_at__isnull=True,
        ).select_related('module_revision')
        for delivery in deliveries:
            validate_delivery_dates(delivery.start_date, delivery.end_date_override, start, end,
                                    weeks=delivery.module_revision.weeks, field='start_date')
    return {
        'training_end_date': automatic_end, 'training_duration_months': training_months,
        'training_end_override': training_end_override, 'apprenticeship_end_override': apprenticeship_end_override,
        'epa_unit': epa_unit,
    }


def validate_group_times(start, end):
    if start.utcoffset() is not None or end.utcoffset() is not None:
        raise ValidationError({'start_time': 'Enter local times without a time zone.'})
    if start >= end:
        raise ValidationError({'end_time': 'End time must be later than start time.'})


def group_cohort(programme, cohort_id, *, group=None, manual=False):
    if group and cohort_id != group.cohort_id:
        raise ValidationError({'cohort_id': 'The parent cohort cannot be changed.'})
    cohorts = programme.cohorts.filter(pk=cohort_id, archived_at__isnull=True)
    if manual:
        cohorts = cohorts.filter(source_system='manual')
    cohort = cohorts.first()
    if cohort is None:
        raise ValidationError({'cohort_id': 'Choose a current cohort in this programme.'})
    return cohort


def validate_module_ksbs(programme, values, *, module=None):
    existing = {ksb.pk for ksb in module.ksbs.all()} if module else set()
    valid = {ksb.pk for ksb in programme.ksbs.filter(pk__in=values) if not ksb.archived_at or ksb.pk in existing}
    if len(values) != len(valid):
        raise ValidationError({'ksb_ids': 'Choose distinct KSBs from this programme; new links cannot use archived KSBs.'})
    return values


def validate_module_topics(weeks, topic_weeks):
    if not 1 <= weeks <= 520:
        raise ValidationError({'weeks': 'Enter a module duration between 1 and 520 weeks.'})
    if len(set(topic_weeks)) != len(topic_weeks) or any(not 1 <= week <= weeks for week in topic_weeks):
        raise ValidationError({'weekly_topics': 'Use distinct week numbers within the module duration.'})


def delivery_group(programme, group_id, cohort_id, *, manual=False):
    groups = CatalogGroup.objects.select_related('cohort').filter(
        pk=group_id, cohort__programme=programme, cohort__archived_at__isnull=True, archived_at__isnull=True,
    )
    if manual:
        groups = groups.filter(cohort__source_system='manual', source_system='manual')
    group = groups.first()
    if not group:
        raise ValidationError({'group_id': 'Choose a current group in this programme.'})
    if cohort_id is not None and group.cohort_id != cohort_id:
        raise ValidationError({'group_id': 'Choose a group that belongs to the selected cohort.'})
    return group


def validate_delivery(programme, group_id, module_id, start, *, end=None, cohort_id=None, revision_id=None,
                      delivery=None, manual=False):
    if delivery and group_id != delivery.group_id:
        raise ValidationError({'group_id': 'The parent group cannot be changed.'})
    group = delivery_group(programme, group_id, cohort_id, manual=manual)
    module = programme.curriculum_modules.filter(pk=module_id, archived_at__isnull=True).first()
    if not module:
        raise ValidationError({'module_id': 'Choose a current curriculum module in this programme.'})
    if CatalogDelivery.objects.filter(group=group, module=module, archived_at__isnull=True).exclude(
        pk=getattr(delivery, 'pk', None),
    ).exists():
        raise ValidationError({'module_id': 'This module is already scheduled in the group.'})
    if revision_id is None and delivery and module_id == delivery.module_id:
        revision = delivery.module_revision
    elif revision_id is not None:
        revision = module.revisions.filter(pk=revision_id).first()
    else:
        revision = module.revisions.first()
    if revision is None:
        raise ValidationError({'module_revision_id': 'Choose a saved revision of this curriculum module.'})
    if delivery and revision.pk != delivery.module_revision_id and delivery.start_date <= timezone.localdate():
        raise ValidationError({'module_revision_id': 'A delivery that has started keeps its recorded curriculum revision.'})
    validate_delivery_dates(start, end, group.cohort.start_date, group.cohort.effective_training_end_date, weeks=revision.weeks)
    return revision


def write_module_content(module, *, ksb_ids=None, topics=None):
    if getattr(module, '_prefetched_objects_cache', None):
        module._prefetched_objects_cache.clear()
    if ksb_ids is not None:
        module.ksbs.set(ksb_ids)
    if topics is not None:
        module.weekly_topics.filter(archived_at__isnull=True).exclude(
            week__in=[topic['week'] for topic in topics],
        ).update(archived_at=timezone.now())
        for topic in topics:
            module.weekly_topics.update_or_create(
                week=topic['week'], defaults={'content': topic['content'], 'archived_at': None},
            )
    return snapshot_module(module)


def snapshot_module(module):
    return CatalogModuleRevision.objects.create(
        module=module, number=module.revision, name=module.name, weeks=module.weeks,
        books=module.books, reviews=module.reviews, lectures=module.lectures,
        ksbs=[{'id': str(ksb.pk), 'code': ksb.code, 'description': ksb.description}
              for ksb in module.ksbs.order_by('code', 'id')],
        weekly_topics=[{'id': str(topic.pk), 'week': topic.week, 'content': topic.content}
                       for topic in module.weekly_topics.filter(archived_at__isnull=True).order_by('week')],
    )


def refresh_ksb_templates(ksb):
    for module in ksb.curriculum_modules.filter(archived_at__isnull=True, source_system='manual'):
        module.revision += 1
        module.save(update_fields=['revision', 'updated_at'])
        snapshot_module(module)
