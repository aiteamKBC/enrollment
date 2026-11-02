"""Authoritative completion policy; already submitted/completed enrolments are exempt."""
from .configuration import KEY
from .models import Assessment

LABEL = "Programme Suitability Assessment"


def legacy_complete(learner):
    return str(getattr(learner, "onboarding_status", "") or "").strip().lower() in {"submitted", "completed"}


def required_for(configuration, learner):
    return bool(configuration and configuration.get("enabled") and not legacy_complete(learner))


def status_for(row):
    if row is not None and row.confirmed_at:
        return "SUBMITTED"
    return "IN_PROGRESS" if row is not None else "NOT_STARTED"


def selection_status_for(row):
    if status_for(row) != "SUBMITTED":
        return status_for(row)
    return "SELECTION_CONFIRMED" if row.selection_confirmed_at else "SUBMITTED_PENDING_CONFIRMATION"


def submission_error(learner):
    """Backstop the existing enrolment-submit endpoint, never trust a draft marker."""
    if legacy_complete(learner):
        return None
    from enrolment_api.wizard_layout import current_layout
    layout = current_layout() or {}
    config = layout.get(KEY)
    row = Assessment.objects.filter(learner_id=learner.pk, confirmed_at__isnull=False).first()
    if selection_status_for(row) == "SELECTION_CONFIRMED":
        if getattr(row, 'selected_programme', '') == 'PCP_L6':
            from .modules_service import saved_choices, selection_complete, pinned_configuration
            from .configuration import ConfigurationError
            choice = saved_choices(row.learner_kind, row.learner_id) if row.modules_configuration is not None else None
            if row.modules_configuration is not None and not selection_complete(row, choice):
                try:
                    row, _, _ = pinned_configuration(row.learner_kind, row.learner_id)
                except ConfigurationError as exc:
                    return str(exc)
            if row.modules_configuration is None or not selection_complete(row, choice):
                return 'Please complete and save your PCP pathway and module choices before continuing.'
        return None
    if not required_for(config, learner):
        return None
    return "Please complete the Programme Suitability Assessment before continuing."
