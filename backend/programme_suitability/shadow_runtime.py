"""Non-blocking runtime diagnostics for canonical learner parity."""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def compare(kind, learner_id, programme_id, selected=None, module_version=None, catalogue_version=None):
    if not getattr(settings, 'PROGRAMME_STRUCTURE_SHADOW_ENABLED', False):
        return None
    try:
        from enrolment_api.wizard_layout import CONN, read_current
        from django.db import connections
        from .programme_structure import shadow_compare_learner
        with connections[CONN].cursor() as cursor:
            current = read_current(cursor)
        if not current:
            return {'programme': programme_id, 'status': 'BLOCKED', 'reasons': ['Published layout unavailable.']}
        report = shadow_compare_learner(current[1], programme_id, selected, layout_version=current[0], module_version=module_version, catalogue_version=catalogue_version)
        reporter = getattr(settings, 'PROGRAMME_STRUCTURE_SHADOW_REPORTER', None)
        if callable(reporter):
            reporter(report)
        logger.info('Programme structure shadow parity kind=%s programme=%s status=%s differences=%s', kind, programme_id, report.get('status'), len(report.get('differences', [])))
        return report
    except Exception as exc:  # diagnostics must never interrupt a learner request
        logger.warning('Programme structure shadow comparison failed kind=%s programme=%s error=%s', kind, programme_id, type(exc).__name__)
        return {'programme': programme_id, 'status': 'BLOCKED', 'reasons': ['Shadow comparison failed.']}


def saved_module_ids(learner_id):
    """Read existing saved selection metadata without invoking selection rules."""
    from .models import Assessment
    row = Assessment.objects.filter(learner_id=learner_id).only('modules_selection').first()
    selection = row.modules_selection if row else None
    if not isinstance(selection, dict):
        return None
    if isinstance(selection.get('modules'), list):
        return list(selection['modules'])
    active = selection.get('pathway')
    by_path = selection.get('modulesByPathway')
    if isinstance(by_path, dict) and isinstance(by_path.get(active), list):
        return list(by_path[active])
    return None
