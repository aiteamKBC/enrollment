"""Read-only adapters for activity already saved by this LMS."""
import hashlib
import json
import math
from datetime import date
from html import escape

from old_otjh.repository import query
from .active_users import completed_hours_value_from_progress


def decoded(value, fallback=None):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return fallback
    return value if value is not None else fallback


def number(value):
    try:
        value = float(value or 0)
        return max(0, value) if math.isfinite(value) else 0
    except (ValueError, TypeError):
        return 0


def stable_id(ref):
    # A stable JavaScript-safe integer across refreshes, independent of row order.
    return int(hashlib.sha256(ref.encode()).hexdigest()[:13], 16)


def profile(learner_id):
    rows = query('''SELECT id, coach_name, coach_email, start_date, end_date, learner_type
                   FROM "Learner".learners WHERE enrolment_id=%s''', [learner_id])
    return rows[0] if len(rows) == 1 else None


def learners(email, search):
    return query('''SELECT u.id, u."Username" AS name, u."Programme" AS programme
        FROM enrolment."Created_users" u
        LEFT JOIN "Learner".learners p ON p.enrolment_id=u.id
        WHERE p.id IS NOT NULL
          AND (%s='' OR lower(btrim(p.coach_email))=%s)
          AND (%s='' OR u."Username" ILIKE %s OR u."Email" ILIKE %s)
        ORDER BY u."Username", u.id''', [email, email, search, f'%{search}%', f'%{search}%'])


def first_evidence_date(learner_id):
    return query('''SELECT min(uploaded_at)::date AS first_date FROM "Learner".evidence_files
                    WHERE learner_id=%s AND status='approved' ''', [str(learner_id)])[0]['first_date']


def monthly_target(learner, month):
    targets = decoded(learner.get('planned_hours_monthly'), {})
    try:
        value = float(targets[month]) if isinstance(targets, dict) else None
        return value if value is not None and math.isfinite(value) and value >= 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def row(ref, at, title, category, *, hours=0, planned=0, note='', group='', ksbs=None, results=None):
    timestamp = at.isoformat() if hasattr(at, 'isoformat') else str(at or '')
    return {'id': stable_id(ref), 'source_ref': ref, 'title': title or category,
            'category': category, 'activity_date': timestamp[:10],
            'activity_time': timestamp[11:19], 'timestamp_label': timestamp[11:19],
            'actual_hours': number(hours), 'planned_hours': number(planned), 'accepted': True,
            'completion_note': note or None, 'group_name': group or None,
            'ksb_codes': sorted(set(ksbs or [])), 'documents': [], 'results': results or []}


def _progress(learner):
    records = query('''SELECT to_jsonb(p) AS payload,
        coalesce(nullif(p.component_title,''),q.title) AS activity_title,
        coalesce((SELECT jsonb_agg(k.ksb_code ORDER BY k.position)
                  FROM "Learner".learner_progress_ksbs k WHERE k.progress_id=p.id),'[]'::jsonb) AS ksbs
        FROM "Learner".learner_progress_entries p
        JOIN "Learner".learners l ON l.id=p.learner_id
        LEFT JOIN curriculum.quizzes q ON q.id::text=p.quiz_ref AND p.kind='quiz'
        WHERE l.enrolment_id=%s AND p.component_link_source IN ('direct','quiz_ref')
          AND p.kind<>'activity_event' AND p.submitted_at IS NOT NULL
        ORDER BY p.submitted_at,p.id''', [learner['id']])
    result = []
    for record in records:
        p = decoded(record['payload'], {})
        ref = f"progress:{p['id']}"
        results = []
        if p.get('kind') == 'quiz':
            results = [{'activity_id': p['id'], 'group_id': 0, 'status': 'Passed' if p.get('passed') else 'Submitted',
                        'quiz_score': p.get('achieved_score'), 'quiz_maximum_score': p.get('total_score'),
                        'quiz_passed': p.get('passed'), 'quiz_attempt_number': p.get('attempt')}]
        item = row(ref, p['submitted_at'], record.get('activity_title') or p.get('component_title') or 'Learning activity',
                   (p.get('component_type') or p['kind']).replace('_', ' ').title(),
                   hours=completed_hours_value_from_progress([p]), planned=p.get('expected_otjh'),
                   note=('AI-generated remedial reading' if p.get('kind') == 'quiz_reading' else p.get('feedback')),
                   group=p.get('module_title'), ksbs=decoded(record['ksbs'], []), results=results)
        item['component_ref'] = p.get('component_ref')
        item['quiz_ref'] = p.get('quiz_ref')
        result.append(item)
    return result


def _attempts(learner):
    from .subject_timing import measured_seconds
    # This table may not be provisioned in installations without subject attempts.
    if not query('SELECT to_regclass(%s) AS table_name', ['"Learner".subject_activity_attempts'])[0]['table_name']:
        return []
    records = query('''SELECT a.id,a.activity_id,a.group_id,a.submitted_at,a.completed,
        a.score_percent,a.passed,a.definition,g.group_name
        FROM "Learner".subject_activity_attempts a
        LEFT JOIN "Last_audit".groups g ON g.group_id=a.group_id
        WHERE a.enrolment_id=%s AND a.aptem_id=%s AND a.submitted_at IS NOT NULL
        ORDER BY a.submitted_at,a.id''', [learner['id'], learner.get('aptem_id')])
    best = {}
    for attempt in records:
        definition = decoded(attempt['definition'], {})
        seconds = measured_seconds(definition.get('studyTime'))
        if attempt['completed'] and seconds is not None:
            key = (attempt['group_id'], attempt['activity_id'], definition.get('catalogue_kind', 'material'))
            best[key] = max(best.get(key, (-1, '')), (seconds, str(attempt['id'])))
    result = []
    for a in records:
        definition = decoded(a['definition'], {})
        results = [{'activity_id': a['activity_id'], 'group_id': a['group_id'],
                    'status': 'Completed' if a['completed'] else 'Submitted',
                    'quiz_score': a['score_percent'], 'quiz_maximum_score': 100 if a['score_percent'] is not None else None,
                    'quiz_passed': a['passed'], 'quiz_attempt_number': None}]
        timing = definition.get('studyTime') or {}
        seconds = measured_seconds(timing)
        key = (a['group_id'], a['activity_id'], definition.get('catalogue_kind', 'material'))
        credited = seconds is not None and best.get(key) == (seconds, str(a['id']))
        # Preserve untimed historical attempts as unavailable, never infer time
        # from opening/submission dates or copy audited source hours.
        result.append(row(f"subject:{a['id']}", timing.get('declaredCompletedAt') or a['submitted_at'], definition.get('title'),
                          'Quiz' if definition.get('catalogue_kind') == 'quiz' else
                          'Reading+Quiz' if definition.get('quiz') else 'Learning', results=results,
                          hours=seconds / 3600 if credited else 0 if seconds is not None else None,
                          group=a['group_name'], note='Measured study time recorded on this platform.' if seconds is not None else
                          'Submitted on this platform. No activity time was recorded.'))
        if seconds is not None:
            result[-1]['accepted'] = bool(a['completed'])
            if not credited:
                result[-1]['completion_note'] = ('Study time is saved in attempt history. Completed hours use '
                    'the longest completed attempt for this activity, counted once.')
    return result


def _reflections(learner, progress):
    records = query('''SELECT to_jsonb(s) AS payload FROM "Learner".learning_reflection_submissions s
        WHERE s.learner_id=%s AND s.status <> 'draft' ORDER BY s.submitted_at,s.id''', [str(learner['id'])])
    by_progress = {p['source_ref']: p for p in progress}
    for record in records:
        s = decoded(record['payload'], {})
        item = by_progress.get(f"progress:{s.get('progress_entry_id')}")
        if item is None:
            item = row(f"reflection:{s['id']}", s.get('submitted_at'), s.get('activity_title'),
                       'Assignment' if s.get('activity_type') == 'assignment' else 'Reflection',
                       hours=s.get('actual_time_hours'), planned=s.get('planned_otjh'), group=s.get('module_title'))
            item['component_ref'] = s.get('component_ref')
            progress.append(item)
        item['completion_note'] = s.get('learning_reflection') or item['completion_note']
        item['ksb_codes'] = sorted(set(item['ksb_codes']) | set(decoded(s.get('ksb_codes'), [])))
        item['accepted'] = s.get('status') not in {'rejected', 'returned', 'resubmission_required'}


def _documents(learner, rows):
    records = query('''SELECT id,original_filename,content_type,component_ref,progress_entry_id,section_ref
        FROM "Learner".evidence_files WHERE learner_id=%s AND status='approved'
        ORDER BY uploaded_at,id''', [str(learner['id'])])
    for doc in records:
        for item in rows:
            matches = item['source_ref'] == f"progress:{doc.get('progress_entry_id')}"
            if not doc.get('progress_entry_id') and doc.get('component_ref'):
                matches = item.get('component_ref') == doc['component_ref']
            if matches:
                item['documents'].append({'id': stable_id(f"document:{doc['id']}"),
                    'display_name': doc['original_filename'], 'content_type': doc['content_type'],
                    'url': f"/learner_api/monthly-logs/{learner['id']}/documents/{doc['id']}/"})


def _meetings(learner):
    from .attendance_confirmation import read_confirmations
    # A past booking is not proof of attendance. Only saved completions count.
    records = query('''SELECT event_key,event_type,scheduled_date,scheduled_time,idempotency_key,
        duration_minutes,notes FROM "Coach".coach_calendar_event
        WHERE lower(btrim(learner_email))=%s AND status='completed'
          AND scheduled_date IS NOT NULL ORDER BY scheduled_date,event_key''', [learner['email'].strip().lower()])
    confirmations = read_confirmations(learner['id'])
    confirmed_reviews = {detail['meetingId'].split(':', 1)[1]: detail['sourceRef']
        for saved in confirmations.values() if (detail := decoded(saved['details'], {})).get('category') == 'Meeting'
        and str(detail.get('meetingId') or '').startswith('imported-review:') and detail.get('sourceRef')}
    # Imported review bookings can acquire another calendar key when moved to
    # another month. Their durable review id still represents one meeting.
    for record in records:
        parts = str(record.get('idempotency_key') or '').split(':')
        if len(parts) == 6 and parts[0] == 'learner-book' and parts[3] == str(learner['id']) and parts[5] in confirmed_reviews:
            record['attendance_ref'] = confirmed_reviews[parts[5]]
    by_source = {r.get('attendance_ref') or f"meeting:{r['event_key']}": row(r.get('attendance_ref') or f"meeting:{r['event_key']}", r['scheduled_date'],
                'Progress Review' if r['event_type'] == 'progress-review' else 'Coaching Session',
                'Review' if r['event_type'] == 'progress-review' else 'Coaching',
                hours=number(r['duration_minutes']) / 60, note=r['notes']) for r in records}
    for saved in confirmations.values():
        detail = decoded(saved['details'], {})
        if detail.get('category') != 'Meeting' or not detail.get('sourceRef'):
            continue
        ref = detail['sourceRef']
        by_source[ref] = row(ref, detail.get('startsAt') or detail['date'], detail['title'],
            'Review' if detail.get('componentType') == 'progress-review' else 'Coaching',
            hours=number(saved['seconds']) / 3600, planned=number(saved['seconds']) / 3600,
            note='Full meeting hours credited after selecting Attend on the meeting date.')
    return list(by_source.values())


def _attendance(learner):
    from .attendance_confirmation import read_confirmations
    records = query('''SELECT o.id,o.scheduled_start,s.module_title AS title,
          max(a.total_attendance_seconds) AS seconds
        FROM curriculum.live_session_attendance a
        JOIN curriculum.live_session_occurrences o ON o.id=a.occurrence_id
        JOIN curriculum.live_sessions s ON s.id=o.live_session_id
        WHERE lower(btrim(a.email))=%s AND a.total_attendance_seconds>0
        GROUP BY o.id,o.scheduled_start,s.module_title ORDER BY o.scheduled_start,o.id''', [learner['email'].strip().lower()])
    by_source = {f"attendance:{r['id']}": row(f"attendance:{r['id']}", r['scheduled_start'], r['title'],
                 'Attendance', hours=number(r['seconds']) / 3600) for r in records}
    for saved in read_confirmations(learner['id']).values():
        detail = decoded(saved['details'], {})
        ref = detail.get('sourceRef')
        if not ref or detail.get('category') == 'Meeting':
            continue
        # The same lecture keeps its exact source reference if a Teams report
        # arrives later. Replace its hours, never add a second attendance row.
        by_source[ref] = row(ref, detail.get('startsAt') or detail['date'], detail['title'], 'Attendance',
            hours=number(saved['seconds']) / 3600, planned=number(saved['seconds']) / 3600,
            group=detail.get('module'), ksbs=detail.get('ksbs'),
            note='Full lecture hours credited after selecting Attend on the lecture date.')
    return list(by_source.values())


def activity_rows(learner):
    rows = _progress(learner)
    _reflections(learner, rows)
    _documents(learner, rows)
    rows.extend(_attempts(learner))
    rows.extend(_meetings(learner))
    rows.extend(_attendance(learner))
    today = date.today().isoformat()
    return [r for r in rows if len(r['activity_date']) == 10 and r['activity_date'] <= today]


def _part(item, **values):
    return {'id': item['id'], 'title': item['title'], 'category': item['category'],
            'url': None, 'html': None, 'quiz': None, **values}


def _answers_html(definition, answers):
    sections = []
    for question in (definition.get('quiz') or {}).get('questions', []):
        selected = answers.get(question['id'], [])
        chosen = [option['text'] for option in question.get('options', []) if option['id'] in selected]
        sections.append(f"<h3>{escape(question['text'])}</h3><p>{escape(', '.join(chosen) or 'No answer')}</p>")
    return '<h2>Saved quiz attempt</h2>' + ''.join(sections) if sections else ''


def _progress_quiz(learner, item):
    from .quizzes import _fetch_quiz
    quiz = _fetch_quiz(item['quiz_ref'])
    if not quiz:
        return None
    records = query('''SELECT a.question_ref,a.chosen_answer_ref,a.is_correct,
        coalesce((SELECT jsonb_agg(c.answer_ref) FROM "Learner".learner_quiz_chosen_answers c
                  WHERE c.quiz_answer_id=a.id),'[]'::jsonb) AS selected
        FROM "Learner".learner_quiz_answers a
        JOIN "Learner".learner_progress_entries p ON p.id=a.progress_id
        JOIN "Learner".learners l ON l.id=p.learner_id
        WHERE p.id=%s AND l.enrolment_id=%s ORDER BY a.position''',
        [item['source_ref'].split(':', 1)[1], learner['id']])
    answered = {str(a['question_ref']): a for a in records}
    sections = []
    for q in quiz['questions']:
        answer = answered.get(str(q['id']))
        if answer is None:
            continue
        selected = {str(value) for value in decoded(answer['selected'], [])}
        if answer.get('chosen_answer_ref') is not None:
            selected.add(str(answer['chosen_answer_ref']))
        choices = [a['text'] for a in q['answers'] if str(a['id']) in selected]
        sections.append(f"<h3>{escape(q['text'])}</h3><p>Your answer: {escape(', '.join(choices) or 'No answer')}</p>"
                        f"<p>{'Correct' if answer['is_correct'] else 'Incorrect'}</p>")
    return _part(item, title=quiz['title'], html='<h2>Saved quiz attempt</h2>' + ''.join(sections)) if sections else None


def activity_content(learner, item):
    parts = []
    ref = item['source_ref']
    if ref.startswith('progress:') and item.get('quiz_ref'):
        part = _progress_quiz(learner, item)
        if part:
            parts.append(part)
    if ref.startswith('subject:'):
        records = query('''SELECT group_id,activity_id,definition,answers FROM "Learner".subject_activity_attempts
            WHERE id=%s AND enrolment_id=%s AND aptem_id=%s AND submitted_at IS NOT NULL''',
            [ref.split(':', 1)[1], learner['id'], learner['aptem_id']])
        if records:
            from .student_activity import _owned_material, _owned_quiz, _definition_for, _local_pdf_urls
            a = records[0]
            definition = decoded(a['definition'], {})
            kind = (learner.get('_profile') or {}).get('learner_type') or 'apprenticeship'
            if definition.get('catalogue_kind') == 'quiz':
                _owned_quiz(kind, learner['id'], a['group_id'], a['activity_id'])
            else:
                _aptem_id, stored = _owned_material(kind, learner['id'], a['group_id'], a['activity_id'])
                material = _local_pdf_urls(_definition_for(stored), kind, learner['id'], a['group_id'], a['activity_id'])
                if material.get('reading_html'):
                    parts.append(_part(item, html=material['reading_html']))
                for media in material.get('media', []):
                    parts.append(_part(item, id=item['id'] + len(parts), url=media['url'], title=media['title']))
            html = _answers_html(definition, decoded(a['answers'], {}))
            if html:
                parts.append(_part(item, id=item['id'] + len(parts), title='Saved quiz attempt', html=html))
    elif item.get('component_ref'):
        from .learner_detail import _video_url_from_settings, component_audio_url, _component_resource_url
        records = query('SELECT title,description,settings_json FROM curriculum.components WHERE id=%s', [item['component_ref']])
        if records:
            c = records[0]
            settings = decoded(c['settings_json'], {})
            html = settings.get('readingContent') or settings.get('assignmentBrief') or ''
            url = _video_url_from_settings(settings) or component_audio_url(settings, item['category'].lower())
            url = url or _component_resource_url(settings)
            if html or url:
                parts.append(_part(item, id=item['id'] + len(parts), html=html, url=url))
            elif c['description']:
                parts.append(_part(item, id=item['id'] + len(parts), html=f"<p>{escape(c['description'])}</p>"))
    if item.get('completion_note'):
        parts.append(_part(item, id=item['id'] + len(parts), title='Saved reflection and feedback',
                           html=f"<p>{escape(item['completion_note'])}</p>"))
    return {'id': item['id'], 'parts': parts}
