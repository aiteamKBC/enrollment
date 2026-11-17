"""Save enrolment fields and mirror learner-specific dates and Skills Coach."""
from django.db import transaction

from .identity import learner_profile_for_source


DATE_FIELDS = ("learner_start_date", "learner_end_date")


def learner_date_values(source):
    return {field: getattr(source, field, None) or None for field in DATE_FIELDS}


def save_enrolment_fields(user, fields):
    profile_fields = {}
    if any(field in fields for field in DATE_FIELDS):
        profile_fields.update(learner_date_values(user))
    if any(field in fields for field in ("coach_name", "coach_email")):
        contact = {
            "coach_name": str(getattr(user, "coach_name", "") or "").strip(),
            "coach_email": str(getattr(user, "coach_email", "") or "").strip(),
        }
        fields = {**fields, **contact}
        profile_fields.update(contact)
        # Imported review ownership follows the Skills Coach only. A Case Owner
        # edit is administrative and must never transfer coaching work.
        # Resolve without opportunistically repairing an identity link.
        profile = learner_profile_for_source(user, heal=False)
        previous = (
            str(getattr(profile, "coach_name", "") or "").strip().casefold(),
            str(getattr(profile, "coach_email", "") or "").strip().casefold(),
        ) if profile is not None else None
        current = (contact["coach_name"].casefold(), contact["coach_email"].casefold())
        if profile is not None and previous != current:
            from coach_api.review_handover import save_assignment
            handover = save_assignment(user, profile, fields, profile_fields)
            if handover is not None and handover[0]:
                return {"reviewHandover": handover[1]}
        else:
            handover = None
    else:
        handover = None
    if not profile_fields:
        user.save(update_fields=list(fields))
        return

    # A failed profile write must also roll back the enrolment edit.
    with transaction.atomic(using="enrolment"):
        user.save(update_fields=list(fields))
        profile = learner_profile_for_source(user)
        if profile is None:
            # Activation copies these values when the profile is created later.
            return {"reviewHandover": handover[1]} if handover is not None else None
        for field, value in profile_fields.items():
            setattr(profile, field, value)
        profile.save(using="enrolment", update_fields=[*profile_fields, "updated_at"])
    return {"reviewHandover": handover[1]} if handover is not None else None
