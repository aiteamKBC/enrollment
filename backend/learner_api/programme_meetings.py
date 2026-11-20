"""Read-only integration of confirmed Enrolment programmes with LMS Reviews ID."""
from datetime import date, datetime
from django.db import DatabaseError
from enrollment_service.lms_reviews_readonly import read_programme_reviews
from programme_suitability.models import Assessment
from programme_suitability.programme_identity import CURRICULUM_PROGRAMME_IDS
from programme_suitability.lms_placement import curriculum_programme_id, label_key, group_key
from .models import LearnerProfile


class MeetingResult(tuple):
    def __new__(cls, rows, reason='', blockers=None):
        result = super().__new__(cls, (rows, reason))
        result.blockers = blockers or []
        return result


def blocked(code, reason='unavailable', **details):
    return MeetingResult([], reason, [{'code': code, **details}])


def day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or '')[:10])
    except ValueError:
        return None


def confirmed_programme_meetings(learner):
    """None retains legitimate legacy/imported flows; confirmed learners use LMS.

    Never derive a programme from a module or a start date from a cohort. The
    LMS learner ID namespace for overrides is LearnerProfile.pk, not enrolment pk.
    """
    from curriculum_api import review_instances, review_otjh
    using = learner._state.db or 'default'
    try:
        row = Assessment.objects.using(using).filter(learner_id=learner.pk, learner_kind='apprenticeship',
            selection_confirmed_at__isnull=False).first()
        if row is None:
            return None
        pid = CURRICULUM_PROGRAMME_IDS.get(row.selected_programme)
        if not pid or curriculum_programme_id('', learner.programme) != pid:
            return blocked('confirmed_programme_conflict')
        start, end = day(learner.start_date), day(learner.end_date)
        if not start or not end or end < start:
            return blocked('saved_learner_dates_required', 'no-dates')
        profiles = list(LearnerProfile.objects.using(using).filter(enrolment_id=learner.pk)[:2])
        if len(profiles) != 1:
            return blocked('review_learner_identity_unverified')
        profile = profiles[0]
        if profile.programme_id and profile.programme_id != pid:
            return blocked('review_programme_identity_conflict')
        raw = read_programme_reviews(pid, profile.pk, start, end)
        if raw['blockers']:
            return MeetingResult([], 'unavailable', raw['blockers'])
        templates = raw['review_templates']
        overrides = raw['review_occurrence_overrides']
        if templates is None or overrides is None:
            return blocked('review_sources_unavailable')
        if len({t['id'] for t in templates}) != len(templates):
            return blocked('duplicate_review_template_identity')
        if any(t.get('programme_id') != pid or not t.get('enabled') or t.get('deleted_at') for t in templates):
            return blocked('review_template_programme_conflict')
        if any(o.get('programme_id') != pid or o.get('learner_id') not in (None, profile.pk)
                or o.get('deleted_at') or o.get('action') != 'skip' for o in overrides):
            return blocked('review_override_identity_unverified')
        scope = {'cohort_id': profile.cohort_id, 'group_id': profile.group_id}
        errors, applicable = [], []
        for template in templates:
            recurrence_errors = {}
            review_instances.reviews.validate_recurrence({'recurrenceInterval': template.get('recurrence_interval'),
                'recurrenceUnit': template.get('recurrence_unit')}, recurrence_errors)
            if recurrence_errors:
                errors.append({'code': 'review_recurrence_unavailable', 'reviewTemplateId': template['id']})
                continue
            applicability = review_instances.curriculum_views.as_json_value(template.get('applicability'), {})
            placement = applicability.get('scope', 'programme')
            if placement not in ('programme', 'cohort', 'group'):
                errors.append({'code': 'review_placement_scope_unavailable', 'reviewTemplateId': template['id']})
                continue
            if placement in ('cohort', 'group'):
                same = (label_key(profile.cohort) == label_key(learner.cohort) if placement == 'cohort'
                    else group_key(profile.group_name) == group_key(learner.group))
                if not scope.get(placement + '_id') or not same:
                    errors.append({'code': 'review_placement_unverified', 'reviewTemplateId': template['id']})
                    continue
            if review_instances.review_applies_to_placement(template, scope):
                applicable.append(template)
        by_review = {}
        for override in overrides:
            target = day(override['occurrence_date'])
            if not target:
                return blocked('review_override_date_unavailable')
            by_review.setdefault(override['review_id'], {})[target.isoformat()] = override

        def occurrences(template, learner_id, status, anchor, window_start, window_end):
            try:
                # Onboarding needs a forecast of the programme commitment, not
                # permission to perform an Active-only review now. Relax only
                # this in-memory projection's status gate; execution continues
                # to receive the original unchanged LMS template everywhere.
                projected_template = ({**template, 'applicable_statuses': []}
                    if status.strip().casefold() == 'onboarding' else template)
                return review_instances.resolve_learner_occurrences(projected_template, learner_id, status, anchor,
                    window_start, window_end, overrides_by_date=by_review.get(template['id'], {}))
            except (ValueError, TypeError, OverflowError, KeyError):
                errors.append({'code': 'review_recurrence_unavailable', 'reviewTemplateId': template['id']})
                return []

        rows = review_otjh.planned_meetings(pid, profile.pk, str(learner.programme_status or ''), start, start, end,
            template_cache={f'otjh:{pid}': applicable}, include_non_otjh=True, occurrence_resolver=occurrences)
        originals = {t['id']: t for t in applicable}
        for meeting in rows:
            meeting['planningDuringOnboarding'] = str(learner.programme_status or '').strip().casefold() == 'onboarding'
            meeting['executionEligibleNow'] = review_instances.learner_is_eligible(
                originals[meeting['reviewTemplateId']], str(learner.programme_status or ''))
        return MeetingResult(rows, 'unavailable' if errors else '' if rows else 'not-eligible' if templates else 'no-reviews', errors)
    except DatabaseError:
        return blocked('enrolment_review_context_unavailable')
