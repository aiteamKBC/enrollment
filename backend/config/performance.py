import logging
import time
from contextlib import ExitStack

from django.conf import settings
from django.db import connections


logger = logging.getLogger('performance')


class PerformanceTimingMiddleware:
    """Measure request and SQL time when PERFORMANCE_DIAGNOSTICS is enabled."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'PERFORMANCE_DIAGNOSTICS', False):
            return self.get_response(request)

        query_count = 0
        database_ms = 0.0

        def measure_query(execute, sql, params, many, context):
            nonlocal query_count, database_ms
            started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                query_count += 1
                database_ms += (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        with ExitStack() as stack:
            for database in connections.all():
                stack.enter_context(database.execute_wrapper(measure_query))
            response = self.get_response(request)

        total_ms = (time.perf_counter() - started) * 1000
        application_ms = max(0.0, total_ms - database_ms)
        # Production headers are exposed only to explicitly allowlisted signed-
        # in diagnostic accounts. A browser header cannot opt itself in.
        from system_audit.performance import diagnostic_account_allowed
        diagnostic_account = diagnostic_account_allowed(getattr(request, 'login_account', None))
        if settings.DEBUG or diagnostic_account:
            response['Server-Timing'] = (
                f'db;dur={database_ms:.1f};desc="{query_count} queries", '
                f'app;dur={application_ms:.1f}, total;dur={total_ms:.1f}'
            )
            response['X-DB-Query-Count'] = str(query_count)
        if diagnostic_account:
            response['X-LMS-Performance-Diagnostic'] = '1'

        threshold = getattr(settings, 'SLOW_REQUEST_THRESHOLD_MS', 750)
        if total_ms >= threshold:
            logger.warning(
                'Slow request method=%s path=%s status=%s total_ms=%.1f db_ms=%.1f queries=%s',
                request.method,
                request.path,
                response.status_code,
                total_ms,
                database_ms,
                query_count,
            )
        return response
