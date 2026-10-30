"""Old-schema compatibility applies only to reads of the optional Phase 2 ledger."""
from contextlib import nullcontext

from django.db import connections, models, transaction, ProgrammingError, OperationalError


class ReconciliationSchemaNotDeployed(ValueError):
    code = "RECONCILIATION_SCHEMA_NOT_DEPLOYED"

    def __init__(self):
        super().__init__("Deploy the review reconciliation migration before using this operation.")


def _missing_ledger(exc, db):
    cause = exc.__cause__
    if db.vendor == "postgresql":
        # UndefinedTable alone is insufficient: a joined overlay may be missing.
        if getattr(cause, "sqlstate", None) != "42P01":
            return False
        diagnostic = getattr(cause, "diag", None)
        return getattr(diagnostic, "message_primary", None) == (
            'relation "Coach.review_source_reconciliation" does not exist'
        )
    if db.vendor == "sqlite":
        from .models import ReviewSourceReconciliation
        return str(exc) == f"no such table: {ReviewSourceReconciliation._meta.db_table}"
    return False


def _optional_read(read, *, using, absent):
    db = connections[using]
    try:
        # A failed PostgreSQL SELECT must not poison a caller's transaction.
        # In autocommit no savepoint/extra transaction is necessary.
        scope = transaction.atomic(using=using) if db.in_atomic_block else nullcontext()
        with scope:
            return read()
    except (ProgrammingError, OperationalError) as exc:
        if not _missing_ledger(exc, db):
            raise
        return absent


def reconciliation_schema_available(*, using="default"):
    from .models import ReviewSourceReconciliation
    db = connections[using]

    def probe():
        with db.cursor() as cursor:
            cursor.execute(f"SELECT 1 FROM {db.ops.quote_name(ReviewSourceReconciliation._meta.db_table)} LIMIT 0")
        return True

    return _optional_read(probe, using=using, absent=False)


def require_reconciliation_schema():
    if not reconciliation_schema_available():
        raise ReconciliationSchemaNotDeployed()


class CompatibleReconciliationQuerySet(models.QuerySet):
    """Evaluate ordinary mapping reads; no catalog polling or stale absence cache.

    Clones retain this class, so first/exists/select_related and filtered reads
    share the contract. A missing ledger never means an unavailable overlay or
    connection is safe to ignore. Reconciliation writes use the normal manager.
    """

    def _fetch_all(self):
        if self._result_cache is None:
            def fetch():
                super(CompatibleReconciliationQuerySet, self)._fetch_all()
                return self._result_cache
            self._result_cache = _optional_read(fetch, using=self.db, absent=[])

    def exists(self):
        return _optional_read(super().exists, using=self.db, absent=False)

    def count(self):
        return _optional_read(super().count, using=self.db, absent=0)
