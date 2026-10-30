"""Dependency-free rules for adding the LMS live register to KBC history."""
import re

LIVE_STATUSES = {'present', 'absent'}


def _session_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def default_lecture_name(module_name, number):
    """The learner workspace's fallback title for an unmatched Teams lecture."""
    if number is None:
        return module_name
    return f'{module_name or "Live session"} — Session {number}'


def build_live_records(rows, occurrences, titles, kbc_id_by_profile, local_date, today, kbc_id=None,
                       lecture_titles=None):
    """Live register rows as KBC-shaped records keyed by KBC learner id.

    Rows whose learner has no stable KBC (Aptem) id, whose occurrence is
    unknown, or whose session is after ``today`` are left out.
    ``lecture_titles`` maps a register row id to its curriculum lecture title.
    """
    lecture_titles = lecture_titles or {}
    records = {}
    for row in rows:
        learner_id = kbc_id_by_profile.get(row['learner_profile_id'])
        if learner_id is None or (kbc_id is not None and learner_id != kbc_id):
            continue
        occurrence = occurrences.get(row['occurrence_id'])
        session_date = local_date(occurrence['scheduled_start']) if occurrence else None
        if session_date is None or session_date > today:
            continue
        number = _session_number(occurrence.get('session_number'))
        module_name = str(row.get('module_title') or titles.get(row['live_session_id']) or '').strip()
        status = str(row['attendance_status'] or '').strip().lower()
        records.setdefault(learner_id, []).append({
            'recordKey': f"live:{row['id']}",
            'learnerId': learner_id,
            'sessionDate': session_date.isoformat(),
            'moduleName': module_name,
            'lectureName': lecture_titles.get(row['id']) or default_lecture_name(module_name, number),
            'sessionNumber': number,
            'attendance': status if status in LIVE_STATUSES else None,
            'source': 'live',
        })
    return records


def _day(record):
    return str(record.get('sessionDate') or '')[:10]


def merge_live_over_kbc(kbc_records, live_records):
    """One learner's history; the live register wins on any date it covers."""
    live_days = {_day(record) for record in live_records}
    kept = [record for record in kbc_records if _day(record) not in live_days]
    return sorted(kept + list(live_records), key=lambda record: (_day(record), record['recordKey']))


def live_days_per_learner(live):
    return max((len({_day(record) for record in records}) for records in live.values()), default=0)


def is_kbc_learner_id(value):
    return bool(re.fullmatch(r'\d{1,20}', value or ''))


# Enrolment rows that only record bookkeeping, not where the learner is.
_IGNORED_ENROLMENT_STATUSES = {'duplicate - merged'}


def enrolment_status_by_aptem(pairs):
    """{aptem id: programme status} from (aptem_id, programme_status) pairs.

    An Aptem id whose enrolment rows disagree is left out rather than guessed,
    so the page falls back to that learner's KBC status.
    """
    statuses = {}
    for aptem_id, status in pairs:
        key = str(aptem_id or '').strip()
        value = str(status or '').strip()
        if not key or not value or value.casefold() in _IGNORED_ENROLMENT_STATUSES:
            continue
        statuses.setdefault(key, set()).add(value)
    return {key: next(iter(values)) for key, values in statuses.items() if len(values) == 1}


def parse_attendance_changes(changes):
    """Validate [{source, recordKey, attendance}] into (live {row id: status}, kbc {key: status}).

    Live register keys look like 'live:<id>'; KBC keys are kbc_attendance."key".
    """
    if not isinstance(changes, list) or not changes or len(changes) > 200:
        raise ValueError('Send between 1 and 200 attendance changes.')
    live, kbc = {}, {}
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError('Each change needs a record and an attendance status.')
        source = str(change.get('source') or '').strip().lower()
        record_key = str(change.get('recordKey') or '').strip()
        status = str(change.get('attendance') or '').strip().lower()
        if status not in LIVE_STATUSES:
            raise ValueError('Attendance must be present or absent.')
        if source == 'live':
            match = re.fullmatch(r'live:(\d{1,18})', record_key)
            if not match:
                raise ValueError('A live register record key is invalid.')
            live[int(match.group(1))] = status
        elif source == 'kbc':
            if not record_key or len(record_key) > 500:
                raise ValueError('A KBC record key is invalid.')
            kbc[record_key] = status
        else:
            raise ValueError('Each change must come from the KBC history or the live register.')
    return live, kbc
