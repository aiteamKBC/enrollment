"""Employer adapters for the existing read-only learner projections.

Callers must first authorize the employer and the source enrolment. These
adapters select the learner perspective; they never invoke learner HTTP views
or the legacy builder's activation, hydration and snapshot persistence.
"""
from old_otjh.service import ServiceError

from . import canonical_learning, dashboard_tabs
from .identity import learner_profile_for_source
from .journal_sources import learner_journal_reads


def without_action_links(value):
    """Retain learning facts while withholding meeting/booking capabilities."""
    if isinstance(value, dict):
        return {key: None if key in {
            'joinUrl', 'meetingLink', 'bookingUrl', 'liveSessionUrl',
            'teamsMeetingUrl', 'extraTeamsMeetingUrl',
        } else without_action_links(item) for key, item in value.items()}
    if isinstance(value, list):
        return [without_action_links(item) for item in value]
    return value


def read_dashboard(source, kind, tab, request):
    with learner_journal_reads():
        # read_tab verifies the canonical profile against this exact enrolment,
        # uses heal=False and installs the same request caches as the learner.
        return without_action_links(dashboard_tabs.read_tab(source, kind, tab, request))


def _prepare_source(source):
    owner = canonical_learning.require_profile(source.pk)
    profile = learner_profile_for_source(source, source.pk, heal=False)
    if profile is None or profile.pk != owner['id'] or profile.enrolment_id != source.pk:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    source._canonical_profile = owner
    source._case_file_profile = profile
    dashboard_tabs.install_request_caches(source)
    return profile


def read_schedule(source, section):
    from .training_plan_dashboard import read_dashboard as read_schedule_dashboard
    with learner_journal_reads():
        # The header must resolve imported memberships as well as native plans.
        _prepare_source(source)
        return without_action_links(read_schedule_dashboard(source, section=section))


def read_plan(source):
    """Compatibility payload for the employer's full plan/KSB observer."""
    from .mappers import to_learner_detail
    from .weekly_learning import build_learning_content_summary
    from .learner_detail import _annotate_otjh, _live_otjh_snapshot, _apply_live_otjh_snapshot

    with learner_journal_reads():
        profile = _prepare_source(source)
        detail = to_learner_detail(source, profile)
        content = build_learning_content_summary(source, source.pk, learning_summary=True,
                                                 include_component_details=True, strict=True)
        for key in ('modules', 'week', 'components', 'retiredQuizComponents'):
            detail[key] = content[key]
        detail['components'], detail['totalExpectedOtjh'] = _annotate_otjh(detail['components'])
        _apply_live_otjh_snapshot(detail, _live_otjh_snapshot(detail, profile))
        return without_action_links(detail)
