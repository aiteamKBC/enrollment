from django.apps import AppConfig


class ManualAuditApiConfig(AppConfig):
    name = 'manual_audit_api'

    def ready(self):
        # Records the manual audit's plan events and the auditor's overrides in
        # the Audit Trail. Raw tables, so nothing is connected to a signal here
        # -- this only declares what the write paths in `audit_trail` may keep.
        # Never fails a start-up: it logs and moves on.
        from system_audit.records import register_manual_audit_records
        register_manual_audit_records()
