from programme_suitability.test_runner import SuitabilityRunner
from django.db import connections


class PlrRunner(SuitabilityRunner):
    def setup_databases(self, **kwargs):
        result = super().setup_databases(**kwargs)
        connections['enrolment'] = connections['default']
        return result
