"""Off-the-job hours contributed by a programme's Reviews.

``review_templates.expected_otjh`` is how many OTJ hours ONE occurrence of a
Review is worth, and ``review_templates.counts_towards_otjh`` is whether those
hours count at all. Both columns have existed since the Review Engine landed
(``sql/2026-09-10_curriculum_review_templates.sql``) but nothing read them --
``reviews.validate_expected_otjh``'s docstring points at "where that flag gets
acted on" as future work. This module is that place.

Two numbers, deliberately kept apart, mirroring how curriculum components
already behave in ``learner_api.learner_detail``:

  * PLANNED comes from RAW occurrences (``resolve_learner_occurrences``) -- a
    forecast. A review the Review Engine says is due adds its hours whether or
    not it has happened yet, exactly as an unstarted component contributes to
    ``totalExpectedOtjh``. Nothing is written to get this number.
  * ACTUAL comes from Review Instances that reached ``completed``. A learner
    only banks the hours once the review is actually done and signed off.

Only ``counts_towards_otjh = true`` templates contribute to either. A Review
with hours authored but the flag off is a review that takes time but is not
claimable as off-the-job training -- that distinction is the whole point of
having two columns, so neither number may fall back to the other.

Every function here is read-only and returns 0.0 rather than raising when
Curriculum is unreachable: OTJH totals are shown on pages that must still
render without them, and a blank hours figure is a better failure than a
blank workspace.
"""
from __future__ import annotations

import logging

from . import review_instances
from . import reviews
from . import views as curriculum_views

logger = logging.getLogger(__name__)

STATUS_COMPLETED = review_instances.STATUS_COMPLETED


def template_otjh_hours(template_row) -> float:
    """Hours ONE occurrence of this template is worth, or 0.0 if it does not
    count. The ``counts_towards_otjh`` gate is applied here and nowhere else,
    so no caller can accidentally count a template that opted out."""
    if not template_row or not bool(template_row.get('counts_towards_otjh')):
        return 0.0
    try:
        hours = float(template_row.get('expected_otjh') or 0)
    except (TypeError, ValueError):
        return 0.0
    return hours if hours > 0 else 0.0


def otjh_template_rows(programme_id):
    """The programme's enabled, OTJH-bearing Review templates.

    Filtered in Python rather than SQL because ``counts_towards_otjh`` and
    ``expected_otjh`` are provisioned by a later migration than the table
    itself -- an older database that has not run it yet returns rows without
    those keys, which must read as "contributes nothing", not blow up.
    """
    if not programme_id:
        return []
    return [
        template_row
        for template_row in review_instances.list_enabled_review_templates(programme_id)
        if template_otjh_hours(template_row) > 0
    ]


def planned_hours(
    programme_id,
    learner_id,
    learner_status,
    learner_start_date,
    window_start,
    window_end,
    *,
    template_cache=None,
):
    """Forecast OTJ hours from every Review occurrence due in the window.

    A raw projection -- see ``resolve_learner_occurrences``. Asking twice for
    the same window returns the same answer and creates nothing, so this is
    safe to call on every page render.
    """
    meetings = planned_meetings(
        programme_id, learner_id, learner_status, learner_start_date,
        window_start, window_end, template_cache=template_cache,
    )
    return round(sum(meeting['hours'] for meeting in meetings), 2)


def planned_meetings(
    programme_id,
    learner_id,
    learner_status,
    learner_start_date,
    window_start,
    window_end,
    *,
    template_cache=None,
):
    """One row per OTJH-bearing Review template, for display beside a plan.

    Same projection ``planned_hours`` sums, kept itemised so a reader can see
    WHERE the hours come from -- a total that rises with no visible cause is
    worse than no total. Each row carries the template's own name (read live,
    never frozen: renaming a Review in Curriculum must rename it here), what
    one occurrence is worth, how many fall in the window, and the product.

    Templates that produce no occurrence in the window are omitted rather than
    listed as zero: they are not part of this learner's plan.
    """
    if not programme_id or not learner_start_date:
        return []

    if template_cache is None:
        template_cache = {}
    cache_key = f'otjh:{programme_id}'
    try:
        if cache_key not in template_cache:
            template_cache[cache_key] = otjh_template_rows(programme_id)
        template_rows = template_cache[cache_key]
    except Exception as exc:
        logger.warning('Could not load OTJH review templates for programme %s: %s', programme_id, exc)
        return []

    meetings = []
    for template_row in template_rows:
        hours = template_otjh_hours(template_row)
        try:
            occurrences = review_instances.resolve_learner_occurrences(
                template_row, learner_id, learner_status, learner_start_date,
                window_start, window_end,
            )
        except Exception as exc:
            logger.warning(
                'Could not resolve review occurrences for OTJH (template=%s learner=%s): %s',
                template_row.get('id'), learner_id, exc,
            )
            continue
        if not occurrences:
            continue
        dates = sorted(
            curriculum_views.format_date(occurrence['targetDate'])
            for occurrence in occurrences
        )
        meetings.append({
            'reviewTemplateId': template_row.get('id'),
            'name': curriculum_views.clean_str(template_row.get('name')),
            'hoursEach': hours,
            'occurrences': len(occurrences),
            'hours': round(hours * len(occurrences), 2),
            'recurrenceLabel': curriculum_views.clean_str(
                occurrences[0].get('recurrenceLabel')
            ),
            # The window this projection actually covers, so the plan can show
            # the same start/end columns it shows for a module.
            'startDate': dates[0],
            'endDate': dates[-1],
        })
    return meetings


def completed_hours(learner_id, *, window_start=None, window_end=None):
    """Banked OTJ hours from this learner's COMPLETED Review Instances.

    Read against the live template, not the instance's ``definition_snapshot``:
    the snapshot freezes a review's FORM so history stays honest, while what a
    review is worth in hours is a curriculum-wide figure a coach may correct
    after the fact. Correcting it must fix every learner's total, the same way
    editing a component's ``expected_otjh`` does.
    """
    if not learner_id:
        return 0.0

    try:
        instances = review_instances.list_review_instances_for_learner(
            learner_id, window_start=window_start, window_end=window_end,
        )
    except Exception as exc:
        logger.warning('Could not load review instances for OTJH (learner=%s): %s', learner_id, exc)
        return 0.0

    completed = [
        instance for instance in instances
        if curriculum_views.clean_str(instance.get('status')) == STATUS_COMPLETED
    ]
    if not completed:
        return 0.0

    hours_by_template: dict[str, float] = {}
    total = 0.0
    for instance in completed:
        template_id = curriculum_views.clean_str(instance.get('review_template_id'))
        if not template_id:
            continue
        if template_id not in hours_by_template:
            try:
                template_row = reviews.get_review_template_row(template_id, include_deleted=True)
            except Exception as exc:
                logger.warning('Could not load review template %s for OTJH: %s', template_id, exc)
                template_row = None
            hours_by_template[template_id] = template_otjh_hours(template_row)
        total += hours_by_template[template_id]
    return round(total, 2)
