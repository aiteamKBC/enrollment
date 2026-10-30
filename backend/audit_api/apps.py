from django.apps import AppConfig


class AuditApiConfig(AppConfig):
    name = "audit_api"

    def ready(self):
        from old_otjh.read_model import register_record_monitor_read_model
        register_record_monitor_read_model()
        # Import for signal registration: Created_users changes affect the
        # global Record Monitor identity/link projection.
        from old_otjh import read_model_signals  # noqa: F401

        # Records the auditor's corrections in the Audit Trail. Raw tables, so
        # nothing is connected to a signal here -- this only declares what the
        # write paths in `audit_trail` may keep. Never fails a start-up: it logs
        # and moves on.
        from system_audit.records import register_audit_records
        register_audit_records()
