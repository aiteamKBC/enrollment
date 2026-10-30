"""Read-only cohort start checks, shared by the dashboard and activity API."""
import re
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone

from .apprenticeship_agreement import _to_date
from .models import EnrolmentUser
from .placement_links import placement_ids


def _month_cohort_key(value):
    """Recognize month/year labels only; never fuzzy-match named cohorts."""
    match = re.fullmatch(r'([a-z]+)\s+(\d{4})', str(value or '').strip().lower())
    if not match:
        return None
    months = ('january', 'february', 'march', 'april', 'may', 'june',
              'july', 'august', 'september', 'october', 'november', 'december')
    month, year = match.groups()
    for index, name in enumerate(months, 1):
        if month in {name, name[:3]}:
            return index, year
    return None


def learning_access(source):
    programme = str(getattr(source, 'programme', '') or '').strip()
    cohort = str(getattr(source, 'cohort', '') or '').strip()
    # The enrolment and active profile have different primary keys; the profile
    # is reached through enrolment_id, never by names. A renamed cohort keeps its
    # id, so the enrolment's old label must not decide which cohort this is.
    cohort_id = placement_ids(source)['cohort_id']
    if cohort_id or (programme and cohort):
        # A copied individual date must not open teaching before the cohort.
        with connections['default'].cursor() as cursor:
            if cohort_id:
                cursor.execute(
                    '''SELECT start_date FROM curriculum.cohorts
                       WHERE cohort_id = %s AND deleted_at IS NULL''', [cohort_id],
                )
            else:
                cursor.execute(
                    '''SELECT start_date FROM curriculum.cohorts
                       WHERE programme_name = %s AND cohort_name = %s AND deleted_at IS NULL
                       ORDER BY updated_at DESC NULLS LAST LIMIT 1''',
                    [programme, cohort],
                )
            row = cursor.fetchone()
            # Imported enrolments use full month names while authoring may use
            # abbreviations. Resolve only a unique active cohort in this exact
            # programme; a missing date or ambiguous alias must remain blocked.
            key = _month_cohort_key(cohort)
            if not cohort_id and row is None and key is not None:
                cursor.execute(
                    '''SELECT cohort_name, start_date FROM curriculum.cohorts
                       WHERE programme_name = %s AND deleted_at IS NULL''',
                    [programme],
                )
                matches = [candidate for candidate in cursor.fetchall()
                           if _month_cohort_key(candidate[0]) == key]
                if len(matches) == 1:
                    row = (matches[0][1],)
        start = _to_date(row[0]) if row else None
    else:
        start = _to_date(getattr(source, 'start_date', None))
    today = timezone.localdate(timezone=ZoneInfo('Europe/London'))
    return {'blocked': start is None or start > today, 'startDate': start.isoformat() if start else ''}


#: Open before the cohort starts, so a learner can prepare work early. Each one
#: only returns generated text or checks, or holds learner-owned draft material;
#: none of them submits, completes or records hours.
DRAFTING_PATHS = frozenset({
    '/learner_api/reflection/transcribe/',
    '/learner_api/reflection/proofread/',
    '/learner_api/reflection/ksb-explanations/',
    '/learner_api/reflection/monthly-reflections/',
    '/learner_api/reflection/learning-statements/',
    '/learner_api/reflection/assignment/check/',
    '/learner_api/reflection/assignment/ai-check/',
    '/learner_api/reflection/assignment/presentation-design/',
    '/learner_api/reflection/assignment/presentation/',
})

#: Saves a draft *or* submits, depending on the body. The gate cannot see the
#: body, so the view refuses the submit half itself (``submission_refusal``).
SUBMISSIONS_PATH = '/learner_api/reflection/submissions/'

#: Evidence upload and delete: files a learner attaches to a draft.
_EVIDENCE_DRAFTING = re.compile(r'^/learner_api/evidence/[^/]+/\d+/(?:upload/|[0-9a-fA-F-]{36}/)$')


def requires_started_programme(path, method='GET'):
    if path.startswith(('/learner_api/time-tracking/', '/learner_api/quizzes/',
                        '/learner_api/videos/', '/learner_api/components/')):
        return True
    if re.match(r'^/learner_api/student-activity/[^/]+/\d+/\d+/\d+/', path):
        return True
    if path in DRAFTING_PATHS or path == SUBMISSIONS_PATH or _EVIDENCE_DRAFTING.match(path):
        return False
    return method not in {'GET', 'HEAD', 'OPTIONS'} and path.startswith((
        '/learner_api/reflection/', '/learner_api/evidence/', '/learner_api/monthly-reports/',
    ))


def _start_refusal(account, *, submitting=False):
    """403 while this learner's cohort has not started; None once it has."""
    try:
        source = EnrolmentUser.all_learners.only('programme', 'cohort', 'start_date').get(pk=account.subject_id)
        access = learning_access(source)
    except EnrolmentUser.DoesNotExist:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    except DatabaseError:
        return JsonResponse({'error': 'Could not check your programme start date. Please try again.',
                             'code': 'programme_access_unavailable'}, status=503)
    if not access['blocked']:
        return None
    if submitting:
        message = (f"Submissions open on {access['startDate']}. Save your work as a draft until then."
                   if access['startDate'] else
                   'Your cohort start date must be confirmed before you can submit. Save your work as a draft until then.')
    else:
        message = (f"Learning activities open on {access['startDate']}. Until then you can view your programme and "
                   "training plan, and save drafts of your work."
                   if access['startDate'] else 'Your cohort start date must be confirmed before you can begin learning.')
    response = JsonResponse({'error': message, 'code': 'programme_not_started', **access}, status=403)
    response['Cache-Control'] = 'private, no-store'
    return response


def refusal(path, account, method='GET'):
    if account.role != 'learner' or not requires_started_programme(path, method):
        return None
    return _start_refusal(account)


def submission_refusal(account):
    """Refuse a learner's *submission* before their cohort starts.

    Drafts are not asked about -- saving one is allowed early. Staff and admins
    acting for a learner are not held back, the same as ``refusal``.
    """
    if getattr(account, 'role', None) != 'learner':
        return None
    return _start_refusal(account, submitting=True)
