"""Executable test fixture only: disposable SQLite, two connections, no network."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    pcp = '--pcp' in sys.argv
    programme = 'PCP_L6' if pcp else 'ME_L4'
    programme_id = 'PROG-PCP-L6' if pcp else 'PROG-ME-L4'
    from django.conf import settings
    from programme_suitability.test_runner import refused
    assert all(d['ENGINE'] == 'django.db.backends.sqlite3' and d['NAME'] == ':memory:' for d in settings.DATABASES.values())
    socket.create_connection = socket.socket.connect = socket.socket.connect_ex = refused
    fixture_root = Path(__file__).resolve().parents[2] / '.cache'
    fixture_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='group-capacity-fixture-', dir=fixture_root) as directory:
        assert Path(directory).resolve().is_relative_to(fixture_root.resolve())
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(directory) / 'capacity.sqlite3'), 'OPTIONS': {'timeout': 2}}}
        import django
        django.setup()
        from django.db import connection, connections, OperationalError
        from django.utils import timezone
        from programme_suitability.models import Assessment, CohortGroupPool, CohortGroupAllocation
        from programme_suitability import group_capacity as capacity
        with connection.schema_editor() as editor:
            for model in (Assessment, CohortGroupPool, CohortGroupAllocation):
                editor.create_model(model)
        pool = CohortGroupPool.objects.create(programme_id=programme_id, cohort_id='home',
            verified_at=timezone.now(), verification_evidence='Disposable fixture', verified_members={'g1': list(range(1, 20)), 'g2': []})
        for identifier in (9101, 9102):
            Assessment.objects.create(learner_id=identifier, learner_kind='apprenticeship', configuration_version=1, configuration={}, pathway='marketing', selected_programme=programme)
        groups = [dict(group_id=f'g{i}', group_name=f'Group {i}', programme_id=programme_id, cohort_id='home') for i in (1, 2)]
        module = {'id': 'social-media', 'title': 'Social Media'}
        offerings = [dict(module_catalogue_id=g['group_id'] + '-module', title='Social Media', cohort_name='Feb 2027',
            cohort_status='planned', start_date='2027-02-01', end_date='2027-05-01', **g) for g in groups]
        def context(row, using, *, lock=False):
            return capacity.CapacityContext(row.learner_id, 'apprenticeship', programme, programme_id, 'home', '', '', (module,), 24, 0)
        overlap = threading.Barrier(2)
        local = threading.local()
        arrivals, results, errors = [], [], []
        def read_groups(*args):
            if not getattr(local, 'overlapped', False):
                local.overlapped = True
                arrivals.append(threading.get_ident())
                overlap.wait(timeout=10)
            return groups
        def confirm(identifier):
            try:
                for attempt in range(10):
                    try:
                        results.append(capacity.allocate('apprenticeship', identifier, 0, pcp_only=pcp)['groupId'])
                        break
                    except OperationalError:
                        if attempt == 9:
                            raise
                        time.sleep(0.03)
            except Exception as error:
                errors.append(repr(error))
            finally:
                connections.close_all()
        with patch.object(capacity, 'context_for', side_effect=context), patch.object(capacity.lms_readonly, 'get_cohort_groups', side_effect=read_groups), patch('programme_suitability.module_schedules.read_all', return_value=offerings):
            threads = [threading.Thread(target=confirm, args=(identifier,)) for identifier in (9101, 9102)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            assert not any(thread.is_alive() for thread in threads)
        assert not errors, errors
        assert len(set(arrivals)) == 2
        assert sorted(results) == ['g1', 'g2'], results
        assert CohortGroupAllocation.objects.filter(pool=pool, group_id='g1').count() == 1
        assert CohortGroupAllocation.objects.get(group_id='g1').seat == 20
        assert CohortGroupAllocation.objects.get(group_id='g2').seat == 1
        print(json.dumps({'groups': sorted(results), 'overlappingConnections': len(set(arrivals))}))
        connections.close_all()


if __name__ == '__main__':
    main()
