"""Shared identity resolution for enrolment rows and learner profiles."""

import logging

from .models import EnrolmentUser, LearnerProfile

logger = logging.getLogger(__name__)


def learner_profile_for_source(source, source_pk=None, *, active_only=False, heal=True, fields=None):
    """Return the profile belonging to a Created_users row.

    ``enrolment_id`` is the real link and is tried first: it is the learner's id
    in ``Created_users``, written when the profile is created and backfilled for
    everything older by ``apply_learner_enrolment_id``.

    Email is kept only as a fallback, for profiles that predate that column and
    could not be matched during the backfill. It is a poor key — the two tables
    have independent primary-key sequences, so email was the only bridge
    available before, but a corrected address silently breaks it and two people
    sharing an address collide. Anything it resolves is repaired in passing, so
    the fallback empties itself over time.

    The primary-key fallback below is narrower still: it only helps legacy source
    rows that genuinely have no email.

    ``heal=False`` resolves the same profile without that repair, for GET reads
    that must never write.
    """
    filters = {"lifecycle_status": "active"} if active_only else {}
    profiles = LearnerProfile.objects.only(*fields) if fields else LearnerProfile.objects

    if source_pk is None:
        source_pk = getattr(source, "pk", None) or getattr(source, "id", None)

    if source_pk is not None:
        profile = profiles.filter(enrolment_id=source_pk, **filters).first()
        if profile is not None:
            return profile

    email = str(getattr(source, "email", "") or "").strip()
    if email:
        profile = profiles.filter(email__iexact=email, enrolment_id__isnull=True, **filters).first()
        if profile is not None:
            # A shared address cannot identify which enrolment owns legacy work.
            twins = list(
                EnrolmentUser.all_learners.filter(email__iexact=email)
                .exclude(pk=source_pk)
                .values_list("pk", flat=True)[:5]
            )
            if twins:
                # Silence here cost a day of diagnosis: the caller sees only a
                # missing profile, and reports it as a missing coach or an
                # inactive learner, while the real fault is that this person was
                # enrolled twice. Say so, with the ids somebody can merge.
                logger.warning(
                    "learner_profile_for_source: %s is enrolled more than once "
                    "(Created_users %s and %s), so no profile can be resolved. "
                    "Merge them with: manage.py merge_duplicate_enrolments "
                    "--pairs <keep>:<retire>",
                    email, source_pk, ", ".join(str(pk) for pk in twins),
                )
                return None
            # Self-healing: record the link we just had to infer, so the next
            # lookup takes the fast, correct path. Best-effort — a failure here
            # must not stop the caller getting their profile.
            if heal and source_pk is not None and profile.enrolment_id != source_pk:
                try:
                    profile.enrolment_id = source_pk
                    profile.save(update_fields=["enrolment_id"])
                except Exception:  # noqa: BLE001 - linking is opportunistic
                    pass
        return profile

    if source_pk is None:
        return None
    return profiles.filter(pk=source_pk, enrolment_id__isnull=True, **filters).first()
