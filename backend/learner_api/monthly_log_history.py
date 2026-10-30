"""Read-only Audit sources for Aptem-linked LMS learners."""
from datetime import date
import time

from old_otjh import repository as repo, service as old
from .training_plan_contract import selected_contract, read_contract, contract_extract_metadata

def enabled(learner):
    return bool(learner.get('aptem_id'))



def reporting_months(start):
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    if not start:
        raise old.ServiceError('The Aptem programme start date needs review.', 'history_source_unavailable', 503)
    year, month = start.year, start.month
    result = []
    while f'{year:04d}-{month:02d}' <= repo.CUTOFF:
        result.append(f'{year:04d}-{month:02d}')
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def contract_targets(learner):
    from . import journal_sources, canonical_learning
    if journal_sources.enabled():
        return {month: {'planned': planned}
                for month, planned in canonical_learning.targets(learner['id']).items()}
    candidates = repo.source_query('''SELECT c.id, c.azure_path, c.document_name AS original_name,
        coalesce(nullif(a.display_name,''),c.document_name) AS document_name,
        c.date, c.fetched_at, c.training_plan_planned_hours, c.raw AS extraction_metadata
        FROM fetching_evidence.aptem_cv_contracts_probe c
        LEFT JOIN "Audit".contract_document_archive a ON a.contract_id=c.id
        WHERE c.learner_id=%s
          AND (lower(btrim(c.current_programme))=lower(btrim(%s))
               OR lower(btrim(c.program_name))=lower(btrim(%s)))
          AND (lower(c.document_name) ~ 'training[[:space:]_-]*plan'
               OR lower(a.display_name) ~ 'training[[:space:]_-]*plan')
          AND a.archived_at IS NULL AND a.deleted_at IS NULL
        ORDER BY coalesce(c.fully_signed_date,c.date) DESC NULLS LAST, c.id DESC''',
        [learner['aptem_id'], learner['programme'], learner['programme']])
    contract = selected_contract(candidates)
    if not contract or not contract.get('azure_path'):
        raise old.ServiceError('The Training Plan is unavailable for this programme.', 'history_source_unavailable', 503)
    try:
        months = read_contract(contract['azure_path'], contract['training_plan_planned_hours'],
                               f"{contract['fetched_at']}:{int(time.time() // 1800)}",
                               contract_extract_metadata(contract.get('extraction_metadata')))
    except Exception as error:
        raise old.ServiceError('The Training Plan could not be read.', 'history_source_unavailable', 503) from error
    if not months:
        raise old.ServiceError('The Training Plan hours need verification.', 'history_source_unavailable', 503)
    return months


def summary(learner):
    profile = repo.report_profile(learner)
    months = reporting_months(profile.get('start_date'))
    record = old.summary({**learner, '_read_only': True})
    existing = {item['month']: item for item in record['months']}
    # The retained journal can predate the LMS activity mirror.  Project only
    # marked LMS activities that are not already represented in the manual
    # rows, keeping the historical signing state unchanged.
    totals_reader = getattr(repo, 'lms_activity_month_totals', None)
    lms_totals = totals_reader(learner) if totals_reader else {}
    provisional_reader = getattr(repo, 'provisional_month_totals', None)
    provisional_totals = provisional_reader(learner) if provisional_reader else {}
    target_warning = None
    try:
        targets = contract_targets(learner) if months else {}
    except old.ServiceError as error:
        if error.code != 'history_source_unavailable':
            raise
        targets = None
        target_warning = (str(error) + ' Activities and actual hours remain available. '
                          'Target hours and OTJH risk cannot be assessed until the plan is available.')
    # Empty months are display-only, never added to required signing months.
    result = []
    for month in months:
        item = {**(existing[month] if month in existing else
            {**old._state(month, None, {}, None, 0), 'is_required': False,
             'can_complete': False, 'total_actual_hours': 0})}
        extra = lms_totals.get(month, {})
        if extra:
            item['row_count'] = int(item.get('row_count') or 0) + extra['row_count']
            item['actual_hours'] = float(item.get('actual_hours') or 0) + extra['actual_hours']
            item['total_actual_hours'] = float(item.get('total_actual_hours') or 0) + extra['actual_hours']
        provisional = provisional_totals.get(month, {})
        if provisional:
            item['actual_hours'] = float(item.get('actual_hours') or 0) + provisional['actual_hours']
            item['total_actual_hours'] = float(item.get('total_actual_hours') or 0) + provisional['actual_hours']
            item['planned_hours'] = float(item.get('planned_hours') or 0) + provisional['planned_hours']
            item['provisional'] = True
        result.append({**item,
                       'training_plan_target': targets.get(month, {}).get('planned', 0.0) if targets is not None else None,
                       'target_warning': target_warning})
    record['months'] = result
    record['profile'] = profile
    return record


def detail(learner, month, *, demo=False):
    state = old.review_month(summary(learner), month)
    rows = repo.month_rows(learner, month, **({'demo': True} if demo else {}))
    # Keep the month header totals aligned with the projected rows.  This is
    # still a read-only view; no legacy journal row is inserted or rewritten.
    state = {**state,
             'row_count': len(rows),
             'planned_hours': sum(float(row.get('planned_hours') or 0) for row in rows),
             'actual_hours': sum(float(row.get('actual_hours') or 0) for row in rows if row.get('accepted')),
             'not_accepted_hours': sum(float(row.get('actual_hours') or 0) for row in rows if not row.get('accepted')),
             'total_actual_hours': sum(float(row.get('actual_hours') or 0) for row in rows)}
    return {**state, 'rows': rows, 'profile': repo.report_profile(learner),
            'demo_only': demo, 'provisional': any(row.get('provisional') for row in rows),
            'snapshot_digest': old.digest(rows)}


def later_rows(learner, last_month):
    """Use Audit report months, not activity dates, just like the journal."""
    if not enabled(learner):
        return {}
    months = repo.query(f"""SELECT DISTINCT month FROM {repo.ROWS}
        WHERE aptem_id=%s AND deleted_at IS NULL
          AND month ~ '^[0-9]{{4}}-(0[1-9]|1[0-2])$'
          AND month>%s AND month<=%s ORDER BY month""",
        [learner['aptem_id'], repo.CUTOFF, last_month])
    return {item['month']: repo.month_rows(learner, item['month']) for item in months}


def merge_rows(lms_rows, audit_rows):
    """Deduplicate only exact source identities, never titles or similar hours."""
    from hashlib import sha256
    rows = [dict(row) for row in lms_rows]
    by_ref = {row['source_ref']: row for row in rows if row.get('source_ref')}
    for original in audit_rows:
        row = dict(original)
        matching = by_ref.get(row.get('source_ref'))
        if matching is not None:
            # Current LMS execution remains authoritative for the same event.
            # Audit is authoritative for its explicit KSB mapping, including [].
            matching['ksb_codes'] = row.get('ksb_codes', [])
            continue
        row['_audit_row_id'] = row['id']
        row['id'] = int(sha256(f"audit:{row['id']}".encode()).hexdigest()[:13], 16)
        rows.append(row)
    return rows
