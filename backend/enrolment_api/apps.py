from django.apps import AppConfig


class EnrolmentApiConfig(AppConfig):
    name = 'enrolment_api'

    def ready(self):
        # Records the enrolment wizard's eight tables in the Audit Trail. Here
        # rather than in system_audit because the models have to be loaded
        # before their signals can be connected, and this is the app that owns
        # them. Never fails a start-up: it logs and moves on.
        from system_audit.records import register_enrolment_wizard_records
        register_enrolment_wizard_records()
