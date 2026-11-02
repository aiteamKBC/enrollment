"""Reuse the existing Azure mail transport; persist delivery state truthfully."""
from html import escape
import logging

from django.db import transaction
from learner_api.models import EnrolmentUser
from login import email_azure
from .models import Assessment

logger = logging.getLogger(__name__)


def learner_email(learner_id):
    return EnrolmentUser.all_learners.filter(pk=learner_id).values_list("email", flat=True).first()


def schedule_review_notification(row):
    """Called inside the review transaction; never sends before that commit."""
    proposed = row.review.get("recommendedProgramme")
    if not proposed or not row.selected_programme or proposed == row.selected_programme:
        return
    if not email_azure.is_configured():
        return  # Persistent review remains visible in the learner's own assessment.
    row.review = {**row.review, "notificationStatus": "PENDING_DELIVERY"}
    record_id, revision = row.pk, row.revision
    transaction.on_commit(lambda: deliver_review_notification(record_id, revision))


def deliver_review_notification(record_id, revision):
    """Future job runners can retry a pending delivery using this entry point."""
    try:
        with transaction.atomic():
            row = Assessment.objects.select_for_update().get(pk=record_id)
            if row.revision != revision or row.review.get("notificationStatus") != "PENDING_DELIVERY":
                return
            email = learner_email(row.learner_id)
            proposed = row.review.get("recommendedProgramme")
            programme = next(p for p in row.configuration["programmes"] if p["id"] == proposed)
            title = f"{programme['title']} Level {programme['level']}"
            text = (f"Admissions has reviewed your programme suitability assessment and suggests considering {title}. "
                    f"Reason: {row.review['reason']}\nYour original programme preference has been preserved. "
                    "This is a preliminary recommendation, not a final admissions decision. "
                    "You can view the review in the CV/Job Description step of your enrolment.")
            sent = False
            if email:
                sent, _detail = email_azure.send_mail(to=email, subject="Your programme suitability review",
                                                    html_body=f"<p>{escape(text)}</p>", text_body=text)
            row.review = {**row.review, "notificationStatus": "SENT" if sent else "DELIVERY_FAILED"}
            row.save(update_fields=["review", "updated_at"])
    except Exception:
        # No credentials, mail contents or learner answers in logs. The durable pending state can be retried.
        logger.warning("Programme suitability review notification could not complete (assessment %s)", record_id)
