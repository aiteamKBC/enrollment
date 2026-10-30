"""Low-overhead, PII-free timing for learner read projections."""
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
from ipaddress import ip_address
import logging
import os
import sys
from time import perf_counter, thread_time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from django.conf import settings
from django.db import connections


log = logging.getLogger(__name__)


class ProjectionPerformance:
    def __init__(self, projection, *, kind, learner_id, section=None):
        self.projection = projection
        self.kind = kind
        self.learner_id = learner_id
        self.section = section
        self.started = perf_counter()
        self.query_count = 0
        self.db_seconds = 0.0
        self.query_count_by_alias = {}
        self.db_seconds_by_alias = {}
        self.stages = {}
        self.current_stage = 'source'
        self.failed_stage = None

    def execute_for(self, alias, execute, sql, params, many, context):
        started = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = perf_counter() - started
            self.query_count += 1
            self.db_seconds += elapsed
            self.query_count_by_alias[alias] = self.query_count_by_alias.get(alias, 0) + 1
            self.db_seconds_by_alias[alias] = self.db_seconds_by_alias.get(alias, 0.0) + elapsed

    def execute(self, execute, sql, params, many, context):
        """Backwards-compatible wrapper used by focused unit tests."""
        return self.execute_for('unknown', execute, sql, params, many, context)

    def wrapper(self, alias):
        def execute(execute_sql, sql, params, many, context):
            return self.execute_for(alias, execute_sql, sql, params, many, context)
        return execute

    @contextmanager
    def stage(self, name):
        previous = self.current_stage
        self.current_stage = name
        started = perf_counter()
        try:
            yield
        except Exception:
            self.failed_stage = name
            raise
        finally:
            self.stages[name] = self.stages.get(name, 0.0) + perf_counter() - started
            self.current_stage = previous

    def fields(self):
        return {
            'projection': self.projection,
            'kind': self.kind,
            'learner_id': self.learner_id,
            'section': self.section or '',
            'duration_ms': round((perf_counter() - self.started) * 1000, 2),
            'query_count': self.query_count,
            'db_duration_ms': round(self.db_seconds * 1000, 2),
            'query_count_by_alias': dict(sorted(self.query_count_by_alias.items())),
            'db_duration_ms_by_alias': {
                alias: round(seconds * 1000, 2)
                for alias, seconds in sorted(self.db_seconds_by_alias.items())
            },
            'stage_durations_ms': {
                name: round(seconds * 1000, 2) for name, seconds in self.stages.items()
            },
        }

    def emit(self, *, status='ok'):
        log.info('learner_projection_perf %s', {**self.fields(), 'status': status})


@contextmanager
def measure_projection(projection, *, kind, learner_id, section=None):
    measurement = ProjectionPerformance(
        projection, kind=kind, learner_id=learner_id, section=section,
    )
    # Learner projections deliberately span the enrolment and default aliases.
    # Measuring one alias made the diagnostic query count look materially lower
    # than the work the request actually performed.
    with ExitStack() as stack:
        for connection in connections.all():
            stack.enter_context(connection.execute_wrapper(measurement.wrapper(connection.alias)))
        try:
            yield measurement
        except Exception:
            measurement.emit(status='error')
            raise
        else:
            measurement.emit()


# The existing stage/execute-wrapper mechanism, opt-in for one local request.
# No SQL, parameters, identities or response values enter this context.
_local_projection = ContextVar('local_projection_stages', default=None)
LOCAL_STAGES = frozenset({
    'dispatch', 'source', 'owner', 'profile', 'assignments', 'ledger',
    'ledger_read', 'ledger_reconcile', 'ledger_completion', 'module_metadata',
    'native_learning', 'native_progress', 'native_content', 'native_quizzes',
    'retained_quizzes', 'attendance_completion', 'live_credits', 'memberships',
    'subject_metadata', 'replacement_settings', 'activity_dates', 'historical_facts',
    'historical_placements', 'card_aggregation', 'module_hours', 'covers',
    'programme_hours', 'upcoming', 'serialize', 'replacement_reconcile', 'other',
})


def projection_stage(name):
    """Request-local stages; disabled callers allocate no timing/SQL collector."""
    measurement = _local_projection.get()
    return measurement.stage(name) if measurement is not None else nullcontext()


LOCAL_OPERATIONS = frozenset({
    'kbc_total', 'kbc_connect', 'kbc_execute', 'kbc_fetch', 'kbc_decode',
    'attendance_schedule', 'attendance_sources', 'teams_reports', 'attendance_roster',
    'attendance_profiles', 'attendance_associations', 'teams_occurrences', 'teams_evidence', 'reviewed_links',
    'identity_rules', 'identity_matching', 'attendance_recovery', 'attendance_catchups',
    'attendance_confirmations', 'attendance_recorded', 'attendance_corrections',
    'shared_series', 'credit_projection', 'assignment_placement', 'assignment_query', 'other',
})


def projection_operation(name):
    """Independent exclusive operation wall/CPU timing under the local gate.

    Operations do not subtract time from the existing parent stage totals.
    Non-CPU wall time can include I/O or scheduling; it is not server SQL time.
    """
    measurement = _local_projection.get()
    return measurement.operation(name) if measurement is not None else nullcontext()


def projection_call(name, function, *args, **kwargs):
    with projection_operation(name):
        return function(*args, **kwargs)


def _local_trace_allowed(request):
    # A request header/query cannot enable diagnostics. Require an explicitly
    # opted-in DEBUG runserver process bound to, and accessed on, loopback.
    if os.environ.get('LMS_MY_LEARNING_TRACE') != '1' or not settings.DEBUG or 'runserver' not in sys.argv:
        return False
    try:
        peer = ip_address(request.META.get('REMOTE_ADDR', ''))
        server = request.META.get('SERVER_NAME', '').strip('[]').lower()
        host = urlsplit('//'+request.META.get('HTTP_HOST', server))
        return (peer.is_loopback and server in {'localhost','127.0.0.1','::1'}
                and host.hostname in {'localhost','127.0.0.1','::1'} and not host.username)
    except (ValueError, TypeError):
        return False


class LocalProjectionPerformance(ProjectionPerformance):
    """Bounded exclusive stage timings, using the existing SQL wrapper hook.

    execute time is client driver wall time, not PostgreSQL execution time.
    Thread CPU outside execute includes Python and C extensions (e.g. fetch
    decoding). Unattributed wall time may include acquisition, fetch, direct
    psycopg calls, cache I/O or scheduling; it is never labelled Python time.
    """
    def __init__(self, request_id):
        self.request_id = str(UUID(str(request_id)))
        self.rows = {}
        self.frames = []
        self.operation_rows = {}
        self.operation_frames = []
        self.started = perf_counter()

    def _row(self, name):
        return self.rows.setdefault(name, {'elapsed':0.0, 'cpu':0.0, 'execute_cpu':0.0, 'database':{}})

    @contextmanager
    def stage(self, name):
        name = name if name in LOCAL_STAGES else 'other'
        frame = {'name':name,'start':perf_counter(),'cpu_start':thread_time(),'children':0.0,'child_cpu':0.0}
        self.frames.append(frame)
        try:
            yield
        finally:
            elapsed,cpu = perf_counter()-frame['start'],thread_time()-frame['cpu_start']
            self.frames.pop()
            row = self._row(name)
            row['elapsed'] += max(0.0,elapsed-frame['children'])
            row['cpu'] += max(0.0,cpu-frame['child_cpu'])
            if self.frames:
                self.frames[-1]['children'] += elapsed
                self.frames[-1]['child_cpu'] += cpu

    @contextmanager
    def operation(self, name):
        name = name if name in LOCAL_OPERATIONS else 'other'
        row = self.operation_rows.setdefault(name, {'elapsed':0.0, 'inclusive_elapsed':0.0, 'cpu':0.0, 'calls':0, 'failures':0, 'sql_calls':0, 'execute_seconds':0.0, 'execute_cpu':0.0})
        frame = {'row':row, 'start':perf_counter(), 'cpu':thread_time(), 'children':0.0, 'child_cpu':0.0}
        self.operation_frames.append(frame)
        row['calls'] += 1
        try:
            yield
        except BaseException:
            row['failures'] += 1
            raise
        finally:
            elapsed, cpu = perf_counter()-frame['start'], thread_time()-frame['cpu']
            self.operation_frames.pop()
            row['inclusive_elapsed'] += elapsed
            row['elapsed'] += max(0.0, elapsed-frame['children'])
            row['cpu'] += max(0.0, cpu-frame['child_cpu'])
            if self.operation_frames:
                self.operation_frames[-1]['children'] += elapsed
                self.operation_frames[-1]['child_cpu'] += cpu

    def execute_for(self, alias, execute, sql, params, many, context):
        row = self._row(self.frames[-1]['name'] if self.frames else 'dispatch')
        # Aliases originate only in configured Django wrappers; cap collector
        # growth even for an unusual dynamically configured connection set.
        if alias not in row['database'] and len(row['database']) >= 16:
            alias = 'other'
        database = row['database'].setdefault(alias, {'calls':0,'seconds':0.0})
        started,cpu_started = perf_counter(),thread_time()
        try:
            return execute(sql,params,many,context)
        finally:
            elapsed, cpu = perf_counter()-started, thread_time()-cpu_started
            database['calls'] += 1
            database['seconds'] += elapsed
            row['execute_cpu'] += cpu
            if self.operation_frames:
                operation = self.operation_frames[-1]['row']
                operation['sql_calls'] += 1
                operation['execute_seconds'] += elapsed
                operation['execute_cpu'] += cpu


    def emit(self):
        stages=[]
        for name,row in self.rows.items():
            execute = sum(item['seconds'] for item in row['database'].values())
            processing_cpu = max(0.0,row['cpu']-row['execute_cpu'])
            stages.append({
                'stage':name,'elapsed_ms':round(row['elapsed']*1000,3),
                'processing_cpu_ms':round(processing_cpu*1000,3),
                'unattributed_wall_ms':round(max(0.0,row['elapsed']-execute-processing_cpu)*1000,3),
                'database':[{'alias':alias,'sql_calls':item['calls'],'client_execute_ms':round(item['seconds']*1000,3)}
                    for alias,item in sorted(row['database'].items())],
                'connection_acquisition_ms':'UNMEASURED',
                'postgresql_server_execution_ms':'UNMEASURED',
            })
        log.info('projection_stage %s', {'request_id':self.request_id,
            'elapsed_ms':round((perf_counter()-self.started)*1000,3),
            'serialization_ms':round(self.rows['serialize']['elapsed']*1000,3) if 'serialize' in self.rows else 'UNMEASURED',
            'stages':stages,
            'operations':[{'operation':name, 'calls':row['calls'], 'failures':row['failures'],
                'elapsed_ms':round(row['elapsed']*1000,3), 'inclusive_elapsed_ms':round(row['inclusive_elapsed']*1000,3), 'thread_cpu_ms':round(row['cpu']*1000,3),
                'processing_cpu_ms':round(max(0.0,row['cpu']-row['execute_cpu'])*1000,3),
                'django_sql_calls':row['sql_calls'], 'django_client_execute_ms':round(row['execute_seconds']*1000,3),
                'non_cpu_wall_ms':round(max(0.0,row['elapsed']-row['cpu'])*1000,3),
                'unwrapped_wall_ms':round(max(0.0,row['elapsed']-row['execute_seconds']
                    -max(0.0,row['cpu']-row['execute_cpu']))*1000,3)}
                for name,row in sorted(self.operation_rows.items())]})


def local_catalogue_trace(view):
    """Wrap authorization through serialization for the initial catalogue only."""
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if kwargs.get('subject_id') is not None or kwargs.get('resource') is not None or not _local_trace_allowed(request):
            return view(request,*args,**kwargs)
        try:
            request_id = str(UUID(str(getattr(request,'request_id',''))))
        except (ValueError,TypeError,AttributeError):
            request_id = str(uuid4())
        measurement = LocalProjectionPerformance(request_id)
        token = _local_projection.set(measurement)
        try:
            with ExitStack() as stack:
                for connection in connections.all():
                    stack.enter_context(connection.execute_wrapper(measurement.wrapper(connection.alias)))
                with measurement.stage('dispatch'):
                    return view(request,*args,**kwargs)
        finally:
            _local_projection.reset(token)
            measurement.emit()
    return wrapped
