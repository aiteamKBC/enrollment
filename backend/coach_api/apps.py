from django.apps import AppConfig


class CoachApiConfig(AppConfig):
    name = 'coach_api'

    def ready(self):
        from .read_model import register_coach_dashboard_read_model
        register_coach_dashboard_read_model()

        # Register read-model refresh events once Django has loaded every model.
        from . import dashboard_signals  # noqa: F401

        # Records coaching meetings and absence reports in the Audit Trail.
        # Here rather than in system_audit because the models have to be loaded
        # before their signals can be connected, and this is the app that owns
        # them. Never fails a start-up: it logs and moves on.
        from system_audit.records import register_coach_records, register_coach_review_records
        register_coach_records()
        # Progress reviews, signatures and marking. Raw tables rather than
        # models, so nothing is connected to a signal here -- registering them
        # is what makes the writes that already reach the recorder land in the
        # trail instead of being dropped.
        register_coach_review_records()
