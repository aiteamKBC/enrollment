"""Dashboard stage metrics without SQL parameters or learner identities."""
from contextlib import ExitStack, contextmanager
from time import perf_counter
import logging

from django.db import connections
logger = logging.getLogger(__name__)


@contextmanager
def dashboard_stage(stage):
    started = perf_counter()
    stats = {"query_count": 0, "query_duration_ms": 0.0, "row_count": 0}
    outcome = "success"

    def execute(execute, sql, params, many, context):
        query_started = perf_counter()
        stats["query_count"] += 1
        try:
            return execute(sql, params, many, context)
        finally:
            stats["query_duration_ms"] += (perf_counter() - query_started) * 1000

    try:
        with ExitStack() as stack:
            for connection in connections.all():
                stack.enter_context(connection.execute_wrapper(execute))
            yield stats
    except BaseException:
        outcome = "failed"
        # Django returns BAD connections to psycopg's discard/replacement path.
        # Never close an active transaction or retry arbitrary dashboard writes.
        for connection in connections.all():
            if connection.errors_occurred and not connection.in_atomic_block:
                try:
                    connection.close_if_unusable_or_obsolete()
                except Exception:
                    logger.exception("dashboard_connection_release_failed")
        raise
    finally:
        duration = round((perf_counter() - started) * 1000, 2)
        fields = {key: round(value, 2) for key, value in stats.items()}
        logger.info(
            "coach_dashboard_stage stage=%s duration_ms=%s query_count=%s "
            "query_duration_ms=%s row_count=%s outcome=%s",
            stage, duration, fields["query_count"], fields["query_duration_ms"], fields["row_count"], outcome,
            extra={"event": "coach_dashboard_stage", "stage": stage, "outcome": outcome,
                   "duration_ms": duration, **fields},
        )
