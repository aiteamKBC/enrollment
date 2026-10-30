"""Read-only Dashboard tab projections; no activation or snapshot repair calls."""
from collections import defaultdict
import logging

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from login.permissions import learner_self_or_staff
from old_otjh.service import ServiceError

from . import canonical_learning as canonical
from .journal_sources import learner_journal_view
from .projection_performance import measure_projection
from .monthly_log_sources import number

log = logging.getLogger(__name__)


def monthly_hours(owner, records, targets, *, signed_months=(), current_month=None):
    """The monthly-log chart's rules without documents, signatures or locks.

    Allocations retain reporting month and seconds, including split entries.
    The original monthly-log summary includes closed months and the open month.
    """
    months = defaultdict(lambda: {'actual_hours': 0, 'not_accepted_hours': 0})
    current = current_month or timezone.localdate().strftime('%Y-%m')
    for record in records:
        for entry in canonical.allocations(record):
            month = entry.get('reporting_month')
            if not month or month > current:
                continue
            value = number(entry.get('actual_seconds')) / 3600
            item = months[month]
            if canonical.counts_as_actual(entry):
                item['actual_hours'] += value
            if entry.get('accepted') is not True:
                item['not_accepted_hours'] += value
    for month in {*targets, *signed_months}:
        if month <= current:
            months[month]
    totals = canonical.metrics_from_records(records, targets)['otjh']
    return {'learner': {'planned_end_date': owner.get('end_date')},
            'months': [{'month': month, **value, 'training_plan_target': targets.get(month)}
                       for month, value in sorted(months.items())],
            'training_plan_totals': {
                'accepted_hours': totals.get('completed_actual', totals.get('actual')),
                'planned_hours': totals['planned']}}


def learning_projection(source, owner, records, *, compact_content=False, monthly_assignments=False):
    from .weekly_learning import build_learning_content_summary
    from .student_activity_access import student_activity_available
    from .student_activity_data import summarize_activities
    from .builder_activity_dates import read_builder_activity_dates
    from .student_activity import builder_subject_links

    options = {'compact_content': True} if compact_content else {}
    weekly = build_learning_content_summary(source, source.pk, learning_summary=True, **options)
    detail = {key: weekly.get(key, []) for key in (
        'modules', 'components', 'retiredQuizComponents', 'quizAttempts', 'videoProgress', 'componentProgress')}
    # Catalogue membership is intentional: a recorded-only projection changes
    # the denominator for imported learners. Evidence bodies stay in Postgres.
    activity = canonical.source_subjects(source.pk, summarize_activities, owner=owner, records=records, include_totals=False) \
        if student_activity_available(source.aptem_id) else None
    if activity is not None:
        activity = {key: activity.get(key) for key in ('subjects', 'activities', 'progress_basis', 'activity_sources')}
        activity['subjects'] = [{key: item[key] for key in (
            'id', 'name', 'module_id', 'catalogue_count', 'accepted_hours') if key in item}
            for item in activity['subjects']]
        activity['activities'] = [{key: item[key] for key in (
            'activity_id', 'group_id', 'group_name', 'activity', 'category', 'completed', 'position',
            'date', 'month', 'week_start', 'week_end', 'date_needs_review', 'date_source',
            'section_title', 'source_activity_id', 'catalogue_kind', 'best_score_percent',
            'quiz_score', 'quiz_maximum_score', *(('planned',) if monthly_assignments else ())) if key in item}
            for item in activity['activities']]
    from .learning_plan import _effective_plan_ids, request_preset_cache
    ids = _effective_plan_ids(source, request_preset_cache(source))
    refs = [f'current:{module_id}' for module_id in ids]
    refs.extend(f"current:{item['moduleId']}" for item in detail['components'] if item.get('moduleId'))
    with connections['enrolment'].cursor() as cur:
        builder = builder_subject_links(cur, source, refs)
        cached_dates = getattr(source, '_dashboard_activity_dates', None)
        if cached_dates is not None and cached_dates[0] == set(ids):
            dates = cached_dates[1]
        else:
            dates = read_builder_activity_dates(cur, ids)
            # Monthly's future-month fallback reads the same module set's dates.
            source._dashboard_activity_dates = (set(ids), dates)
    metadata = {'covers': {}, 'activity_dates': dates,
                'current_subjects': [builder[ref] for ref in refs[:len(ids)] if ref in builder],
                'builder_subjects': builder}
    return {'activity': activity, 'detail': detail, 'metadata': metadata}


def install_request_caches(source):
    """Fresh per-request reuse: one group-preset read serves every effective-plan
    resolution, and each Builder module title is read once across plan and learning reads."""
    source._plan_preset_cache = {}
    source._builder_link_cache = {}
    source._training_activity_dates = None


def read_tab(source, kind, tab, request, *, canonical_contract=False):
    from .identity import learner_profile_for_source

    owner = canonical.require_profile(source.pk)
    source._canonical_profile = owner
    profile_options = {}
    if tab in ('monthly', 'training', 'overview'):
        from .training_plan_dashboard import TRAINING_PLAN_PROFILE_FIELDS
        profile_options['fields'] = TRAINING_PLAN_PROFILE_FIELDS
    profile = learner_profile_for_source(source, source.pk, heal=False, **profile_options)
    if profile is None or profile.pk != owner['id'] or profile.enrolment_id != source.pk:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    source._case_file_profile = profile
    install_request_caches(source)
    if tab == 'training':
        from .dashboard_training import read_training
        return read_training(source, kind, owner, profile, request)
    if tab == 'monthly':
        from .dashboard_monthly import read_monthly
        return read_monthly(source, kind, owner, profile, request,
                            **({'canonical_contract': True} if canonical_contract else {}))
    if tab == 'overview':
        from .dashboard_overview import read_overview
        return read_overview(source, kind, owner, profile, request)
    raise LookupError('Dashboard not found.')


@require_GET
@learner_self_or_staff(kwarg='pk')
@learner_journal_view
def dashboard_tab(request, kind, pk, tab, canonical_contract=False):
    from .learner_detail import SOURCE_MODELS
    model = SOURCE_MODELS.get(kind)
    if model is None or tab not in ('monthly', 'training', 'overview'):
        return JsonResponse({'error': 'Dashboard not found.'}, status=404)
    try:
        from .case_file_sources import source_for_case_file
        with measure_projection('dashboard-' + tab, kind=kind, learner_id=pk) as measurement:
            source = source_for_case_file(request, kind, pk)
            if source is None:
                from .training_plan_dashboard import TRAINING_PLAN_SOURCE_FIELDS
                source = model.all_learners.only(*dict.fromkeys([
                    *TRAINING_PLAN_SOURCE_FIELDS, 'username', 'employer_id', 'learning_plan', 'training_plan',
                ])).get(pk=pk)
            with measurement.stage(tab):
                payload = read_tab(source, kind, tab, request,
                                   **({'canonical_contract': True} if canonical_contract else {}))
    except model.DoesNotExist:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    except ServiceError as error:
        return JsonResponse({'error': str(error)}, status=error.status)
    except LookupError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except DatabaseError:
        log.warning('Dashboard tab unavailable: %s', tab, exc_info=True)
        return JsonResponse({'error': 'Could not load your dashboard. Please retry.'}, status=503)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response
