"""Versioned, scoped one-time transfer from enrolment to the study database."""
import hashlib
import hmac
import json
import time

DOCUMENTS = (
    'Apprenticeship_Agreements', 'Enrolment_Documents', 'Enrolment_Reviews',
    'Extended_ILR', 'ILR_Documents', 'Review_Eligibility', 'Review_Health_Safety',
    'Review_RPL', 'Review_ULN_Privacy', 'Training_Plan_Documents', 'Wizard_Cv_Job',
    'Wizard_Ilr_Learner_Details', 'Wizard_Introduction', 'Wizard_Ksb_Assessments',
    'Wizard_Personal_Details', 'Wizard_Plr', 'Wizard_Plr_Records', 'Wizard_Policy_Acks',
    'Wizard_Skills_Radar', 'Written_Agreements',
)
PLAN_TABLES = ('learner_training_plan_modules', 'learner_training_plan_weeks', 'learner_training_plan_components')
EVENT_TYPES = ('first-session', 'eligibility-review', 'workspace', 'training-plan', 'uln-privacy')
ALLOWED = {
    'enrolment.Created_users', 'enrolment.Employers', 'Learner.learners',
    'login.Login_accounts', 'Learner.coach_calendar_event', 'Learner.learner_free_courses',
    'Learner.learner_ksb_assignments',
    *('enrolment.' + name for name in DOCUMENTS), *('Learner.' + name for name in PLAN_TABLES),
}
MAX_BODY = 20 * 1024 * 1024


class Conflict(ValueError):
    """Identity/ownership differs; never silently merge it."""


def signature(secret, timestamp, body):
    return hmac.new(secret.encode(), str(timestamp).encode() + b'.' + body, hashlib.sha256).hexdigest()


def authenticated(secret, timestamp, supplied, body, now=None):
    try:
        recent = abs((time.time() if now is None else now) - int(timestamp)) <= 60
    except (ValueError, TypeError):
        return False
    return len(secret) >= 32 and recent and hmac.compare_digest(signature(secret, timestamp, body), supplied or '')


def validate(payload):
    if not isinstance(payload, dict) or payload.get('version') != 1:
        raise Conflict('Unsupported handoff contract.')
    rows = payload.get('rows')
    if not isinstance(rows, dict) or set(rows) - ALLOWED:
        raise Conflict('Unsupported handoff table.')
    if any(not isinstance(items, list) or any(not isinstance(row, dict) for row in items) for items in rows.values()):
        raise Conflict('Invalid handoff rows.')
    users = rows.get('enrolment.Created_users', [])
    accounts = rows.get('login.Login_accounts', [])
    profiles = rows.get('Learner.learners', [])
    staff = payload.get('existingStaff')
    if len(users) != 1 or len(accounts) != (0 if staff else 1) or len(profiles) > 1:
        raise Conflict('One learner and one login account are required.')
    user = users[0]
    uid = user.get('id')
    if not isinstance(uid, int) or not user.get('uuid') or user.get('Learner_type') not in ('apprenticeship', 'commercial'):
        raise Conflict('A stable learner identity is required.')
    if staff:
        if not isinstance(staff, dict) or not staff.get('uuid') or str(staff.get('email','')).strip().lower() != str(user.get('Email','')).strip().lower():
            raise Conflict('A linked staff identity must match this learner.')
    else:
        account = accounts[0]
        if account.get('Subject_type') != 'learner' or account.get('Role') != 'learner' or account.get('Subject_id') != uid:
            raise Conflict('Only this learner login may be transferred.')
        if str(account.get('Email', '')).strip().lower() != str(user.get('Email', '')).strip().lower():
            raise Conflict('Learner and login identities differ.')
    if profiles and (profiles[0].get('enrolment_id') != uid or profiles[0].get('uuid') != user['uuid']):
        raise Conflict('The learner profile belongs to another identity.')
    profile_ids = {row['id'] for row in profiles}
    if user.get('learner_id') is not None and user['learner_id'] not in profile_ids:
        raise Conflict('The enrolment profile link belongs to another identity.')
    for name in DOCUMENTS:
        if any(row.get('Learner_id') != uid or row.get('Learner_kind', user['Learner_type']) != user['Learner_type'] for row in rows.get('enrolment.' + name, [])):
            raise Conflict('An enrolment document belongs to another learner.')
    if any(row.get('id') != user.get('Employer_id') for row in rows.get('enrolment.Employers', [])):
        raise Conflict('An employer belongs to another learner.')
    for table in ('learner_training_plan_modules', 'learner_free_courses', 'learner_ksb_assignments'):
        if any(row.get('learner_id') not in profile_ids for row in rows.get('Learner.' + table, [])):
            raise Conflict('A learning assignment belongs to another learner.')
    for table, parent, column in (
        ('learner_training_plan_weeks', 'learner_training_plan_modules', 'plan_module_id'),
        ('learner_training_plan_components', 'learner_training_plan_weeks', 'plan_week_id'),
    ):
        ids = {row['id'] for row in rows.get('Learner.' + parent, [])}
        if any(row.get(column) not in ids for row in rows.get('Learner.' + table, [])):
            raise Conflict('A learning plan row belongs to another learner.')
    for row in rows.get('Learner.coach_calendar_event', []):
        if row.get('learner_id') not in {uid, *profile_ids} or row.get('event_type') not in EVENT_TYPES or str(row.get('learner_email','')).strip().lower() != str(user.get('Email','')).strip().lower():
            raise Conflict('Only this learner enrolment meetings may be transferred.')
    return user


def encode(payload):
    return json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
