"""Selected-session metrics: durations/counts only, never SQL or learner data."""
from contextlib import ExitStack, contextmanager
from time import perf_counter
import logging

from django.db import connections

log = logging.getLogger(__name__)


class AttendanceTiming:
    def __init__(self):
        self.stages = {}
        self.active = None

    @contextmanager
    def stage(self, name):
        previous = self.active
        self.active = name
        stats = self.stages.setdefault(name, {'duration_ms': 0, 'queries': 0, 'query_ms': 0, 'slowest_query_ms': 0})
        started = perf_counter()
        try:
            yield
        finally:
            stats['duration_ms'] += (perf_counter() - started) * 1000
            self.active = previous

    @contextmanager
    def request(self):
        started = perf_counter()

        def execute(call, sql, params, many, context):
            stats = self.stages.get(self.active)
            query_started = perf_counter()
            try:
                return call(sql, params, many, context)
            finally:
                if stats is not None:
                    elapsed = (perf_counter() - query_started) * 1000
                    stats['queries'] += 1
                    stats['query_ms'] += elapsed
                    stats['slowest_query_ms'] = max(stats['slowest_query_ms'], elapsed)

        try:
            with ExitStack() as stack:
                for connection in connections.all():
                    stack.enter_context(connection.execute_wrapper(execute))
                yield self
        finally:
            log.info('coach_attendance_detail total_ms=%.2f stages=%s',
                     (perf_counter() - started) * 1000,
                     {name: {key: round(value, 3) for key, value in stats.items()}
                      for name, stats in self.stages.items()})
