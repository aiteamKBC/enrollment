"""PostgreSQL regression checks using owned synthetic rows, always cleaned up."""
import copy
import json
import threading
import time
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction, DatabaseError
from django.test import RequestFactory
from django.utils import timezone

from programme_suitability import modules_views
from programme_suitability.configuration import defaults as assessment_defaults
from programme_suitability.models import Assessment
from programme_suitability.modules_rules import available_modules
from programme_suitability.modules_service import published_modules


class Command(BaseCommand):
    help = 'Verify module performance, ILR lock isolation and two-session conflicts on PostgreSQL using temporary synthetic rows.'

    def handle(self, *args, **options):
        if connections['default'].vendor != 'postgresql':
            raise CommandError('PostgreSQL is required; SQLite cannot verify these row locks.')
        learner_id = -(int(uuid.uuid4().hex[:10], 16) + 1000000000000)
        version, config = published_modules()
        path = next(p for p in config['programmes'] if p['id'] == 'PCP_L6')['pathways'][0]
        modules = available_modules(path['modules'])
        optional = [m['id'] for m in modules if not m.get('isMandatory')]
        count = path['requiredOptionalModulesCount']
        if count is None or count < 1 or len(optional) <= count:
            raise CommandError('This regression requires a configured path with at least one alternative optional module.')
        choice = {'programme': 'PCP_L6', 'pathway': path['id'], 'configurationVersion': version,
                  'modulesByPathway': {path['id']: optional[:count]}}
        factory = RequestFactory()
        results = {'samples': []}

        def request(selection, expected):
            req = factory.put('/', json.dumps({**selection, 'expectedRevision': expected}), content_type='application/json')
            req.login_account = SimpleNamespace(pk=99, role='learner', subject_id=learner_id, subject_type='learner', is_active=True)
            req._dont_enforce_csrf_checks = True
            return modules_views.learner_modules(req, kind='apprenticeship', learner_id=learner_id)

        def timed(selection, expected):
            queries = []

            def record(execute, sql, params, many, context):
                queries.append({'operation': sql.strip().split()[0].upper(), 'ilr': 'Extended_ILR' in sql,
                                'lock': 'FOR UPDATE' in sql.upper()})
                return execute(sql, params, many, context)

            with ExitStack() as stack:
                for alias in ('default', 'enrolment'):
                    stack.enter_context(connections[alias].execute_wrapper(record))
                start = time.perf_counter()
                response = request(selection, expected)
                elapsed = round((time.perf_counter() - start) * 1000, 1)
            sample = {'status': response.status_code, 'ms': elapsed, 'queries': len(queries),
                      'ilrWrites': sum(q['ilr'] and q['operation'] in ('INSERT', 'UPDATE', 'DELETE') for q in queries),
                      'ilrLocks': sum(q['ilr'] and q['lock'] for q in queries),
                      'ilrReads': sum(q['ilr'] and q['operation'] == 'SELECT' for q in queries)}
            results['samples'].append(sample)
            assert sample['ilrWrites'] == sample['ilrLocks'] == 0
            return response

        try:
            Assessment.objects.create(learner_id=learner_id, learner_kind='apprenticeship', configuration_version=1,
                configuration=assessment_defaults(), pathway='project-controls', selected_programme='PCP_L6',
                confirmed_at=timezone.now(), selection_confirmed_at=timezone.now(),
                modules_configuration=config, modules_configuration_version=version)
            with connections['enrolment'].cursor() as cursor:
                cursor.execute('INSERT INTO enrolment."Extended_ILR" ("Learner_kind","Learner_id","Learner_signed","Provider_signed","Completed","Created_at","Updated_at","Eligibility_evidence","Answers","Wizard_draft") VALUES (%s,%s,false,false,false,now(),now(),%s::jsonb,%s::jsonb,%s::jsonb) RETURNING id',
                    ['apprenticeship', learner_id, '[]', json.dumps({'sentinel': 'ILR unchanged'}),
                     json.dumps({'programmeModules': choice, 'cvJob': {'experienceText': 'Synthetic performance check'}})])
                ilr_id = cursor.fetchone()[0]
                cursor.execute('SELECT row_to_json(t) FROM enrolment."Extended_ILR" t WHERE id=%s', [ilr_id])
                before = cursor.fetchone()[0]
            with patch.object(modules_views, 'learner_record', return_value=SimpleNamespace(pk=learner_id)):
                first = timed(choice, 0)
                assert first.status_code == 200, first.content
                saved = json.loads(first.content)
                assert saved['selectionComplete']
                same = timed(choice, 0)
                assert json.loads(same.content)['selectionRevision'] == saved['selectionRevision']
                # A real Extended ILR row lock blocks the previous query pattern.
                waiting = threading.Event()
                old_result = {}

                def previous_lock_pattern():
                    started = time.perf_counter()
                    try:
                        with transaction.atomic(using='default'), connections['default'].cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '1500ms'")
                            cursor.execute('SELECT pg_backend_pid()')
                            old_result['pid'] = cursor.fetchone()[0]
                            waiting.set()
                            started = time.perf_counter()
                            cursor.execute('SELECT id FROM enrolment."Extended_ILR" WHERE id=%s FOR UPDATE', [ilr_id])
                    except DatabaseError as exc:
                        old_result.update(sqlstate=getattr(exc.__cause__, 'sqlstate', None) or getattr(exc.__cause__, 'pgcode', None), ms=round((time.perf_counter() - started) * 1000, 1))
                    finally:
                        connections['default'].close()

                with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cursor:
                    cursor.execute('SELECT id FROM enrolment."Extended_ILR" WHERE id=%s FOR UPDATE', [ilr_id])
                    thread = threading.Thread(target=previous_lock_pattern)
                    thread.start()
                    assert waiting.wait(10), 'Lock worker did not start'
                    for _ in range(6):
                        cursor.execute('SELECT cardinality(pg_blocking_pids(%s))', [old_result['pid']])
                        results['observedBlockers'] = cursor.fetchone()[0]
                        if results['observedBlockers']:
                            break
                        time.sleep(0.05)
                    alternate = copy.deepcopy(choice)
                    alternate['modulesByPathway'][path['id']] = optional[1:count + 1]
                    isolated = timed(alternate, saved['selectionRevision'])
                    assert isolated.status_code == 200, isolated.content
                    saved = json.loads(isolated.content)
                    thread.join(10)
                    assert not thread.is_alive(), 'Lock worker did not finish'
                assert old_result.get('sqlstate') == '55P03', old_result
                assert results['observedBlockers'] > 0
                old_result.pop('pid')
                results['oldIlrLockQuery'] = old_result
                # Two sessions start at the same revision: only the first edit wins.
                stale_revision = saved['selectionRevision']
                winner = timed(choice, stale_revision)
                assert winner.status_code == 200
                loser = timed(alternate, stale_revision)
                assert loser.status_code == 409
                results['twoSessionConflict'] = True
                # An assessment lock fails fast rather than waiting for a timeout.
                busy = {}

                def competing_save():
                    try:
                        start = time.perf_counter()
                        response = request(alternate, json.loads(winner.content)['selectionRevision'])
                        busy.update(status=response.status_code, ms=round((time.perf_counter()-start)*1000, 1))
                    finally:
                        connections['default'].close()
                        connections['enrolment'].close()

                with transaction.atomic(using='default'):
                    Assessment.objects.select_for_update().get(learner_id=learner_id)
                    thread = threading.Thread(target=competing_save)
                    thread.start(); thread.join(10)
                    assert not thread.is_alive()
                assert busy['status'] == 503, busy
                results['busyAssessment'] = busy
                retry = timed(alternate, json.loads(winner.content)['selectionRevision'])
                assert retry.status_code == 200
                data = json.loads(retry.content)
                row = Assessment.objects.get(learner_id=learner_id)
                assert row.modules_selection == data['selection']
                assert data['selectionComplete']
            with connections['enrolment'].cursor() as cursor:
                cursor.execute('SELECT row_to_json(t) FROM enrolment."Extended_ILR" t WHERE id=%s', [ilr_id])
                assert cursor.fetchone()[0] == before
            results.update(ilrUnchanged=True, persistedAndComplete=True, configurationVersion=version)
        finally:
            with connections['default'].cursor() as cursor:
                cursor.execute('DELETE FROM programme_suitability_assessments WHERE learner_id=%s', [learner_id])
            with connections['enrolment'].cursor() as cursor:
                cursor.execute('DELETE FROM enrolment."Extended_ILR" WHERE "Learner_id"=%s AND "Learner_kind"=%s', [learner_id, 'apprenticeship'])
        results['syntheticRowsCleaned'] = True
        self.stdout.write(json.dumps(results))
