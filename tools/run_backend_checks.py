"""Run only database-free regression cases, with sockets blocked before setup."""
import argparse
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
if '--source' in sys.argv:
    sys.argv.remove('--source')
    sys.path.insert(0, str(ROOT.parent / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'enrollment_service.settings_test'
os.environ['CURRICULUM_WARM'] = '0'
os.environ['ENGLAND_HOLIDAY_AUTO_SYNC'] = '0'
os.environ['API_REQUIRE_AUTH'] = '1'


def refused(*args, **kwargs):
    raise AssertionError('External network calls are forbidden in enrollment regression tests.')


socket.create_connection = refused
socket.socket.connect = refused
socket.socket.connect_ex = refused

import django
django.setup()

from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner
from django.conf import settings
import unittest

if any(value['ENGINE'] != 'django.db.backends.sqlite3' or value['NAME'] != ':memory:' for value in settings.DATABASES.values()):
    raise SystemExit('The regression runner requires in-memory SQLite only.')

parser = argparse.ArgumentParser()
parser.add_argument('labels', nargs='*', default=['enrolment_api'])
args = parser.parse_args()
runner = DiscoverRunner(verbosity=1)
suite = runner.build_suite(args.labels)


def leaves(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from leaves(case)
        else:
            yield case


cases = list(leaves(suite))
unsafe = [type(case).__name__ for case in cases if not isinstance(case, SimpleTestCase) or getattr(case, 'databases', set())]
if unsafe:
    raise SystemExit('Database-dependent suites require the separate verified PostgreSQL runner: ' + ', '.join(sorted(set(unsafe))))
runner.run_checks(databases=[])
result = runner.run_suite(unittest.TestSuite(cases))
raise SystemExit(0 if result.wasSuccessful() else 1)
