from django.apps import AppConfig


class LearnerApiConfig(AppConfig):
    name = 'learner_api'

    def ready(self):
        from .read_model import register_learner_home_read_model
        register_learner_home_read_model()

        # Additive read-model events only; every receiver fails open while the
        # feature flags/outbox remain disabled during staged deployment.
        from . import read_model_signals  # noqa: F401

        # psycopg3 reads `json` columns as dicts, but Django only text-loads
        # `jsonb`, so JSONField 500s on `json` columns (SECURITY_AUDIT.md A22).
        # Register a text loader for both, before any connection is used.
        from .pg_json_compat import register_json_text_loaders
        register_json_text_loaders()

        # Registers the two-database consistency checks (see checks.py and
        # ENROLMENT_GAP_ANALYSIS.md 7.2). Import for the @register side effect.
        from . import checks  # noqa: F401

        # Records learner, staff, employer and organisation saves in the Audit
        # Trail. Here rather than in system_audit because the models have to be
        # loaded before their signals can be connected, and this is the app that
        # owns them. Never fails a start-up: it logs and moves on.
        from system_audit.records import (
            register_enrolment_journey_records,
            register_enrolment_records,
            register_learner_records,
        )
        register_enrolment_records()
        # And the enrolment itself: reviews and their detail sheets, the signed
        # agreements and ILR documents, and the learner's delivery profile.
        # Those are the actions an audit is asked about after the fact; the
        # records above are only what the actions left behind.
        register_enrolment_journey_records()
        # And what the learner themselves submits: monthly reports and evidence
        # files. Raw tables whose write paths record themselves; this only
        # declares what they may keep.
        register_learner_records()
