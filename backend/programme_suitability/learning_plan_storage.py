"""Canonical Enrolment plan storage shared by programme confirmation flows."""
from learner_api.mappers import get_training_plan, training_plan_field
from learner_api.models import EnrolmentUser

ACADEMIC_SOURCES = {'programme_suitability_fixed', 'programme_suitability_pcp'}


def protected_plan(source, profiles):
    # An explicit [] is an intentional saved/cleared plan, not an empty slot.
    return (any(getattr(source, field, None) not in (None, '')
                for field in ('learning_plan', 'training_plan'))
            or bool(get_training_plan(source)) or any(p.learning_plan for p in profiles))


def write_plan(source, plan, using):
    # Caller owns the transaction and source lock. No status/mirror/document hooks.
    EnrolmentUser.all_learners.using(using).filter(pk=source.pk).update(
        **{training_plan_field(source): plan})
