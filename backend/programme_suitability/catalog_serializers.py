import re
from collections.abc import Mapping

from rest_framework import serializers

from . import catalog_services as services
from .catalog_models import (
    CatalogCohort, CatalogDelivery, CatalogGroup, CatalogKsb, CatalogModule, CatalogModuleRevision, CatalogProgramme,
    validate_delivery_dates,
)


METADATA = ('id', 'revision', 'source_system', 'external_id', 'source_revision', 'last_synced_at', 'archived_at', 'updated_at')


class CatalogSerializer(serializers.ModelSerializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            return super().to_internal_value(data)
        unknown = data.keys() - {name for name, field in self.fields.items() if not field.read_only}
        if unknown:
            raise serializers.ValidationError({key: 'This field is not writable.' for key in sorted(unknown)})
        return super().to_internal_value(data)

    def value(self, attrs, field, default=None):
        return attrs[field] if field in attrs else getattr(self.instance, field, default)

    def unique_code(self, value, queryset):
        return services.normalized_code(value, queryset, max_length=self.fields['code'].max_length,
                                        record_id=getattr(self.instance, 'pk', None))

    class Meta:
        read_only_fields = METADATA


class ProgrammeSerializer(CatalogSerializer):
    code = serializers.CharField(max_length=80)

    class Meta(CatalogSerializer.Meta):
        model = CatalogProgramme
        fields = METADATA + (
            'name', 'code', 'level', 'description', 'total_cost', 'epa_cost', 'epao',
            'funding_band_maximum', 'lecture_hours', 'status',
        )

    def validate_status(self, value):
        if value == CatalogProgramme.Status.ARCHIVED:
            raise serializers.ValidationError('Use the archive action to archive a programme.')
        return value

    def validate(self, attrs):
        if 'code' in attrs:
            attrs['code'] = self.unique_code(attrs['code'], CatalogProgramme.objects.all())
        services.validate_programme_cost(self.value(attrs, 'total_cost'), self.value(attrs, 'epa_cost'))
        return attrs


class ProgrammeListSerializer(ProgrammeSerializer):
    counts = serializers.SerializerMethodField()

    class Meta(ProgrammeSerializer.Meta):
        fields = ProgrammeSerializer.Meta.fields + ('counts',)

    def get_counts(self, programme):
        return {
            'cohorts': programme.cohort_count,
            'groups': programme.group_count,
            'scheduled_modules': programme.scheduled_module_count,
        }


class CohortSerializer(CatalogSerializer):
    training_end_date = serializers.DateField(source='effective_training_end_date', read_only=True)
    automatic_training_end_date = serializers.DateField(source='training_end_date', read_only=True)
    duration_days = serializers.IntegerField(read_only=True)
    apprenticeship_end_date = serializers.DateField(read_only=True)
    apprenticeship_duration_months = serializers.IntegerField(read_only=True, allow_null=True)
    training_duration_months = serializers.IntegerField(min_value=1, max_value=120)

    class Meta(CatalogSerializer.Meta):
        model = CatalogCohort
        fields = METADATA + (
            'name', 'start_date', 'training_end_date', 'epa_period', 'epa_unit',
            'training_duration_months', 'duration_days', 'apprenticeship_end_date',
            'apprenticeship_duration_months',
            'automatic_training_end_date', 'training_end_override', 'apprenticeship_end_override',
        )
        read_only_fields = METADATA + ('training_end_date', 'epa_unit')

    def validate(self, attrs):
        saved = self.instance
        start, epa = self.value(attrs, 'start_date'), self.value(attrs, 'epa_period')
        training_months = self.value(attrs, 'training_duration_months')
        training_override = self.value(attrs, 'training_end_override')
        apprenticeship_override = self.value(attrs, 'apprenticeship_end_override')
        if saved:
            schedule_unchanged = (start, epa, training_months) == (
                saved.start_date, saved.epa_period, saved.training_duration_months,
            )
            if schedule_unchanged and (training_override, apprenticeship_override) == (
                saved.training_end_override, saved.apprenticeship_end_override,
            ):
                return attrs
            if training_months is None and not schedule_unchanged:
                raise serializers.ValidationError({'training_duration_months': 'Enter a planned duration to change the saved start date or EPA period.'})
            if training_months is not None and saved.epa_unit != CatalogCohort.EpaUnit.MONTHS and 'epa_period' not in attrs:
                raise serializers.ValidationError({'epa_period': 'Enter the new EPA period in months before recalculating.'})
        attrs.update(services.calculate_cohort_schedule(
            start, training_months, epa, cohort=saved, training_end_override=training_override,
            apprenticeship_end_override=apprenticeship_override,
        ))
        return attrs


class LocalTimeField(serializers.TimeField):
    def to_internal_value(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}(?::[0-9]{2})?', value):
            raise serializers.ValidationError('Enter a local time as HH:MM or HH:MM:SS.')
        return super().to_internal_value(value)


class GroupSerializer(CatalogSerializer):
    cohort_id = serializers.UUIDField()
    start_time = LocalTimeField()
    end_time = LocalTimeField()

    class Meta(CatalogSerializer.Meta):
        model = CatalogGroup
        fields = METADATA + ('cohort_id', 'name', 'coach_id', 'weekday', 'start_time', 'end_time')

    def validate(self, attrs):
        services.group_cohort(self.context['programme'], self.value(attrs, 'cohort_id'), group=self.instance, manual=True)
        services.validate_group_times(self.value(attrs, 'start_time'), self.value(attrs, 'end_time'))
        if 'coach_id' in attrs:
            services.validate_staff(attrs['coach_id'], 'coach')
        return attrs


class KsbSerializer(CatalogSerializer):
    class Meta(CatalogSerializer.Meta):
        model = CatalogKsb
        fields = METADATA + ('code', 'description')

    def validate(self, attrs):
        if 'code' in attrs:
            attrs['code'] = self.unique_code(attrs['code'], self.context['programme'].ksbs.all())
        return attrs

    def update(self, instance, validated_data):
        changes_description = any(field in validated_data and validated_data[field] != getattr(instance, field)
                                  for field in ('code', 'description'))
        saved = super().update(instance, validated_data)
        if changes_description:
            services.refresh_ksb_templates(saved)
        return saved


class TopicSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    week = serializers.IntegerField(min_value=1, max_value=520)
    content = serializers.CharField(max_length=5000)

    def to_internal_value(self, data):
        if isinstance(data, dict) and data.keys() - {'week', 'content'}:
            raise serializers.ValidationError('A topic accepts a week number and content only.')
        return super().to_internal_value(data)


class ModuleSerializer(CatalogSerializer):
    ksb_ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=500, write_only=True)
    weekly_topics = TopicSerializer(many=True, required=False, max_length=520, write_only=True)

    current_revision = serializers.SerializerMethodField()

    class Meta(CatalogSerializer.Meta):
        model = CatalogModule
        fields = METADATA + ('name', 'weeks', 'books', 'reviews', 'lectures', 'ksb_ids', 'weekly_topics', 'current_revision')

    def get_current_revision(self, instance):
        prefetched = getattr(instance, 'latest_revisions', None)
        revision = instance.revisions.first() if prefetched is None else next(iter(prefetched), None)
        return {'id': str(revision.pk), 'number': revision.number} if revision else None

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result['ksb_ids'] = [str(ksb.pk) for ksb in instance.ksbs.all()]
        result['weekly_topics'] = TopicSerializer(
            [topic for topic in instance.weekly_topics.all() if not topic.archived_at], many=True,
        ).data
        return result

    def validate(self, attrs):
        weeks = self.value(attrs, 'weeks')
        topics = attrs.get('weekly_topics')
        topic_weeks = (
            [topic['week'] for topic in topics] if topics is not None else
            [topic.week for topic in self.instance.weekly_topics.all() if not topic.archived_at] if self.instance else []
        )
        services.validate_module_topics(weeks, topic_weeks)
        if 'ksb_ids' in attrs:
            services.validate_module_ksbs(self.context['programme'], attrs['ksb_ids'], module=self.instance)
        return attrs

    def _save_related(self, instance, ksb_ids, topics):
        instance.latest_revisions = [services.write_module_content(instance, ksb_ids=ksb_ids, topics=topics)]
        return instance

    def create(self, validated_data):
        ksb_ids = validated_data.pop('ksb_ids', None)
        topics = validated_data.pop('weekly_topics', None)
        return self._save_related(super().create(validated_data), ksb_ids, topics)

    def update(self, instance, validated_data):
        ksb_ids = validated_data.pop('ksb_ids', None)
        topics = validated_data.pop('weekly_topics', None)
        return self._save_related(super().update(instance, validated_data), ksb_ids, topics)


class PinnedCurriculumSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogModuleRevision
        fields = ('id', 'number', 'name', 'weeks', 'books', 'reviews', 'lectures', 'ksbs', 'weekly_topics')


class DeliverySerializer(CatalogSerializer):
    group_id = serializers.UUIDField()
    cohort_id = serializers.UUIDField(required=False, write_only=True)
    module_id = serializers.UUIDField(required=False)
    module_revision_id = serializers.UUIDField(required=False, write_only=True)
    new_template = ModuleSerializer(required=False, write_only=True)
    curriculum = PinnedCurriculumSerializer(source='module_revision', read_only=True)
    end_date = serializers.DateField(source='end_date_override', required=False, allow_null=True)
    end_date_override = serializers.DateField(read_only=True, allow_null=True)
    tutor_id = serializers.UUIDField(required=False)

    class Meta(CatalogSerializer.Meta):
        model = CatalogDelivery
        fields = METADATA + (
            'group_id', 'cohort_id', 'module_id', 'module_revision_id', 'new_template',
            'curriculum', 'start_date', 'end_date', 'end_date_override', 'tutor_id',
        )

    def to_representation(self, instance):
        result = super().to_representation(instance)
        result['end_date'] = self.fields['end_date'].to_representation(instance.end_date)
        return result

    def validate(self, attrs):
        if self.instance is None and not attrs.get('tutor_id'):
            raise serializers.ValidationError({'tutor_id': 'Select a tutor to schedule this module.'})
        if 'tutor_id' in attrs:
            services.validate_staff(attrs['tutor_id'], 'tutor')
        programme = self.context['programme']
        group_id, cohort_id = self.value(attrs, 'group_id'), attrs.pop('cohort_id', None)
        module_id, revision_id = self.value(attrs, 'module_id'), attrs.pop('module_revision_id', None)
        start, end = self.value(attrs, 'start_date'), self.value(attrs, 'end_date_override')
        if 'new_template' in attrs:
            if self.instance is not None or module_id is not None or revision_id is not None:
                raise serializers.ValidationError({'new_template': 'Create a template only when scheduling a new module without an existing template.'})
            group = services.delivery_group(programme, group_id, cohort_id, manual=True)
            validate_delivery_dates(start, end, group.cohort.start_date, group.cohort.effective_training_end_date,
                                    weeks=attrs['new_template']['weeks'])
            return attrs
        if module_id is None:
            raise serializers.ValidationError({'module_id': 'Select an existing template or create a new one.'})
        attrs['module_revision'] = services.validate_delivery(
            programme, group_id, module_id, start, end=end, cohort_id=cohort_id,
            revision_id=revision_id, delivery=self.instance, manual=True,
        )
        return attrs

    def create(self, validated_data):
        template = validated_data.pop('new_template', None)
        if template is not None:
            module = self.fields['new_template'].create({**template, 'programme': self.context['programme']})
            validated_data.update(module=module, module_revision=module.latest_revisions[0])
        return super().create(validated_data)
