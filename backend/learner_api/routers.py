"""Database router: send Neon-backed apps' models to the `enrolment` DB.

`learner_api`, `enrolment_api` and `login` all map unmanaged tables on Neon:
the first two in the `enrolment` schema, `login` in the `auth` schema beside it
(the credentials belong next to the people they identify — see login/models.py).
Everything else (django.contrib.auth, sessions, admin, migrations) stays on
`default` (SQLite). Those tables are created outside Django — by the apps'
`apply_*` management commands — so migrations never touch them.
"""

APP_LABELS = frozenset({"learner_api", "enrolment_api", "login"})
ENROLMENT_DB = "enrolment"
# Aliases that must never receive a Django migration: their schemas are created
# and owned outside this project.
NON_MIGRATED_DATABASES = frozenset({ENROLMENT_DB, "audit", "audit_clone", "kbc_attendance"})


class EnrolmentRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label in APP_LABELS:
            return ENROLMENT_DB
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in APP_LABELS:
            return ENROLMENT_DB
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # Don't assert cross-database relations; leave the decision open.
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in APP_LABELS and hints.get("learner_schema_migration"):
            return db == "default"
        # These apps' models are unmanaged (managed=False) — never migrate them.
        if app_label in APP_LABELS:
            return False
        # Keep every other app off the Neon databases entirely. The audit
        # branches carry externally-created schemas (Last_audit, Audit, …) and
        # their DDL is applied by reviewed setup commands, never by migrate —
        # so `migrate --database=audit_clone` must be a no-op, not a surprise
        # django_migrations table on an audit branch.
        if db in NON_MIGRATED_DATABASES:
            return False
        return None
