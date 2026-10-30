from django.apps import AppConfig


class ProgressReviewsApiConfig(AppConfig):
    name = 'progress_reviews_api'

    def ready(self):
        # Records progress-review pack generation runs in the Audit Trail. Raw
        # table, so nothing is connected to a signal here -- this only
        # declares what `runs._record_run` may keep. Never fails a start-up:
        # it logs and moves on.
        from system_audit.records import register_progress_review_records
        register_progress_review_records()
