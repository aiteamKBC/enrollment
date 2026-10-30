"""Read-only My Learning catalogue and owned, module-scoped material projection.

Initial reads aggregate narrow identity facts, never build learner-detail,
student-activity or dashboard responses. Global identity/lineage reconciliation
is deliberately separate from selected-module content loading.
"""
from collections import Counter, defaultdict
import re
from copy import copy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from login.permissions import learner_self_or_staff
from old_otjh.service import ServiceError

from . import canonical_learning as canonical
from .journal_sources import learner_journal_view, enabled as journal_enabled
from .learner_detail import SOURCE_MODELS
from .learning_plan import effective_training_plan, request_preset_cache
from .progress_rules import progress_counts_as_achieved
from .projection_performance import projection_stage, local_catalogue_trace

SOURCE_FIELDS = ('id', 'aptem_id', 'email', 'programme', 'cohort', 'group',
                 'learning_plan', 'training_plan', 'modules', 'weeks', 'components', 'learner_type', 'username')
UK = ZoneInfo('Europe/London')


def percent(done, total):
    from math import floor
    return floor(done / total * 10000 + .5) / 100 if total else 0


def catalogue_module_rows(cur, ids, *, identity_metadata):
    """Card metadata and native identities, without constructing a timetable.

    Keep the same stored dates, visibility and educational-session rule as
    dashboard_training.module_rows. Holiday slots/outcomes are not card inputs.
    """
    from .training_plan_dashboard import rows, plan_module, is_educational_live_session_week
    from .student_activity import _builder_cover_url
    from .subject_origins import imported_course_links
    cur.execute('''SELECT m.module_catalogue_id AS id,m.title,m.cover_image_url,
        m.start_date,m.end_date,m.tutor_name,m.programme_id,m.programme_name,
        m.cohort_name,m.group_name,m.total_otjh,m.sessions_number,m.source_type,m.source_id,
        m.session_week_day,m.session_start_time,m.session_end_time,m.cohort_id,
        to_jsonb(m)->'session_overrides' AS session_overrides
        FROM curriculum.modules m WHERE m.module_catalogue_id=ANY(%s)
          AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent,'')<>'')
        ORDER BY m.title''',[ids])
    found = rows(cur)
    modules = {r['id']:plan_module({**r,'weeks_number':None}) for r in found}
    cur.execute('''WITH live_counts AS (
        SELECT c.module_catalogue_id,c.week_id,count(*) AS live_session_count
        FROM curriculum.components c WHERE c.module_catalogue_id=ANY(%s)
          AND lower(replace(coalesce(c.type,''),'-','_'))='live_session'
          AND (c.deleted_at IS NULL OR COALESCE(c.deleted_via_parent,'')<>'')
        GROUP BY c.module_catalogue_id,c.week_id
    ) SELECT w.id,w.module_catalogue_id,w.week_number,w.title,w.display_order,
        coalesce(live_counts.live_session_count,0) AS live_session_count
        FROM curriculum.weeks w LEFT JOIN live_counts
          ON live_counts.module_catalogue_id=w.module_catalogue_id AND live_counts.week_id=w.id
        WHERE w.module_catalogue_id=ANY(%s)
          AND (w.deleted_at IS NULL OR COALESCE(w.deleted_via_parent,'')<>'')
        ORDER BY w.display_order,w.week_number,w.id''',[ids,ids])
    weeks = rows(cur)
    for week in weeks:
        module = modules.get(week['module_catalogue_id'])
        if module and is_educational_live_session_week(week['title']):
            module['educational_session_count'] += max(0,int(week['live_session_count'] or 0))
    identity_metadata.update(module_titles={r['id']:r['title'] for r in found},
        weeks=[(r['id'],r['module_catalogue_id'],r['title'],r['week_number'],r['display_order']) for r in weeks],
        covers={f"current:{r['id']}":_builder_cover_url(r.get('cover_image_url')) for r in found},
        quiz_modules={r['id']:{'module':r['title'],'programmeId':r.get('programme_id') or '',
                              'programme':r.get('programme_name') or ''} for r in found},
        schedule_modules={r['id']: {**r, 'module_catalogue_id': r['id']} for r in found},
        schedule_components=[],
        live_credit_components=[],
        imported_courses=imported_course_links([(r['id'],r.get('source_id')) for r in found
            if r.get('source_type')=='mba-legacy' and re.fullmatch(r'[0-9]+',str(r.get('source_id') or ''))],
            [{'id':r['id']} for r in found]))
    return modules


def session_instant(value):
    """Compare authored instants without using the host's local time zone."""
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    return (at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at).astimezone(timezone.utc)


def course_facts(owner, records, courses):
    """Minimal catalogue identities for exact denominator/lineage and card search.

    No materials, attempts, reporting ledgers or activity response dictionaries.
    The shared canonical allocator retains source/journal precedence and verified
    membership exclusions. One canonical row contributes hours once per course.
    """
    from .course_catalogue_display import retain_learner_schedule
    definitions = canonical.query('''SELECT a.source_activity_id,a.source_activity_title,a.source_activity_type,
        a.source_section_ref,a.source_section_title,a.source_position AS position,
        c.source_course_ref,
        jsonb_build_object('quiz_id',a.source_payload->'quiz_id','activity_id',a.source_payload->'activity_id',
            'parent_activity_id',a.source_payload->'parent_activity_id','section_title',a.source_payload->'section_title',
            'section_order',coalesce(a.source_payload->'section_order',a.source_payload#>'{live_catalogue_recovery,section_order}'),
            'component_order',coalesce(a.source_payload->'component_order',a.source_payload#>'{live_catalogue_recovery,component_order}'),
            'created_at',a.source_payload->'created_at','created_at_utc',a.source_payload->'created_at_utc') AS source_payload
        FROM curriculum.source_activities a
        JOIN curriculum.source_courses c ON c.id=a.source_course_id AND c.deleted_at IS NULL
        WHERE a.source_course_id=ANY(%s) AND a.deleted_at IS NULL AND a.source_system='old_lms'
        ORDER BY c.id,a.source_position NULLS LAST,a.id''', [[c['id'] for c in courses]]) if courses else []
    from .progress_demo import catalogue as demo_catalogue
    definitions = demo_catalogue(courses, [dict(d) for d in canonical.learner_course_catalogue(courses, definitions)])
    by_key = {(str(d['source_course_ref']), d['source_activity_id']): d for d in definitions}
    done, started, hours, record_ids = set(), set(), defaultdict(float), defaultdict(set)
    for record, placements in canonical.course_record_placements(by_key, records, include_catalogue=True):
        if not placements:
            continue
        is_started = str(record.get('activity_status') or '').strip().lower().replace('-', '_') in {'started','in_progress','submitted','failed','completed','passed'}
        is_done = canonical.counts_as_completed(record, include_source_evidence='completed' in record)
        is_actual = canonical.counts_as_actual(record)
        actual_hours = canonical.recorded_seconds(record) / 3600 if is_actual else 0
        record_id = str(record['id'])
        counted = set()
        for definition in placements:
            course = str(definition['source_course_ref'])
            key = (course, definition['source_activity_id'])
            retain_learner_schedule(definition, record, completed=is_done)
            if is_started:
                started.add(key)
            record_ids[key].add(record_id)
            if is_done:
                done.add(key)
            if course not in counted and is_actual:
                hours[course] += actual_hours
            counted.add(course)
    return definitions, done, started, hours, record_ids


def native_done(component, native):
    previous = component.get('previousLearning') or {}
    if previous.get('completed') is True or (str(component.get('type') or '').replace('-', '_') == 'live_session' and component.get('attendanceCompleted') is True):
        return True
    facts = native.get('_completion')
    if facts is None:
        facts = {'components': {p.get('componentId') for bucket in ('videoProgress', 'componentProgress')
                  for p in native.get(bucket, []) if p.get('componentId') and progress_counts_as_achieved(p.get('kind'), p.get('passed'))},
                 'passed_components': {a['componentId'] for a in native.get('quizAttempts', []) if a.get('componentId') and a.get('passed') is True},
                 'passed_quizzes': {str(a.get('quizId')) for a in native.get('quizAttempts', []) if not a.get('componentId') and a.get('passed') is True}}
        native['_completion'] = facts
    if component.get('isQuiz'):
        return component.get('componentId') in facts['passed_components'] or str((component.get('quizMeta') or {}).get('quizId')) in facts['passed_quizzes']
    return bool(component.get('componentId')) and component['componentId'] in facts['components']


def next_title(items, today):
    """The catalogue's nextLearningWeek rule, using only card-preview facts."""
    groups = {}
    for item in items:
        placement = item.get('schedule') or {}
        start, end = placement.get('week_start'), placement.get('week_end')
        raw = placement.get('date')
        if not start and raw:
            at = date.fromisoformat(raw[:10])
            start = (at - timedelta(days=at.weekday())).isoformat()
        if start and not end:
            end = (date.fromisoformat(start) + timedelta(days=6)).isoformat()
        source, label = placement.get('date_source'), item.get('week') or item.get('section_title') or ''
        month = placement.get('month')
        month = month if month and len(month) == 7 else None
        special = 'introduction' if source == 'introduction' else 'extra' if source == 'extra_activity' else ''
        key = 'introduction' if special == 'introduction' else 'extra:' + label if special else (
            start or item.get('weekId') or (f'{month}:{label or "activities"}' if month else label or 'undated'))
        rank = -1 if special == 'introduction' else 2 if special else 0 if start or month else 1
        group = groups.setdefault(key, {'rank': rank, 'start': None if special else start,
            'end': None if special else end, 'sort': start or month or key, 'items': []})
        group['items'].append(item)
    def natural(value):
        return tuple((0,int(part)) if part.isdigit() else (1,part.casefold()) for part in re.split(r'(\d+)',value))
    ordered = sorted(groups.values(), key=lambda g: (g['rank'], natural(g['sort'])))
    pending = [g for g in ordered if any(not i['completed'] for i in g['items'])]
    chosen = next((g for g in pending if g['start'] and g['start'] <= today <= (g['end'] or g['start'])),
                  pending[0] if pending else ordered[0] if ordered else None)
    if not chosen:
        return None
    entries = sorted(chosen['items'], key=lambda i: ((i.get('schedule') or {}).get('date') or '', i.get('position', 0)))
    return next((i['title'] for i in entries if not i['completed']), None)


def assemble_cards(native, current, courses, definitions, done, started, hours, metadata, records, modules, credits, *, search=None):
    """Programme-wide counts with the same explicit import/replacement identities as My Learning."""
    from .progress_transfer_policy import merged_percent
    from .progress_demo import credit as demo_credit
    from .training_plan_dashboard import _canonical_module_progress, module_record_index
    from .live_session_progress import supplement_module_hours
    from .course_catalogue_display import catalogue_display_context, read_export_placements, display_schedule
    # Required only for the visible next-activity preview. SQL expands narrow
    # placement metadata; no course curriculum JSON is transferred to Python.
    if search is None:
        with projection_stage('historical_placements'):
            definitions = catalogue_display_context(definitions, read_export_placements(canonical.query, courses))
    legacy = defaultdict(list)
    for d in definitions:
        course, ref = str(d['source_course_ref']), d['source_activity_id']
        kind, _, ident = ref.partition(':')
        legacy[course].append({'activity_id': f'catalogue:{course}:{ref}', 'group_id': int(course),
            'source_activity_id': int(ident) if ident.isdigit() else 0, 'catalogue_kind': kind,
            'completed': (course, ref) in done, 'started': (course, ref) in started,
            '_course_ref': course, 'title': d['source_activity_title'], 'section_title': d.get('source_section_title'),
            'schedule': display_schedule(d) or {}, 'position': d.get('position') or 0})
    components = [*native.get('components', []), *native.get('retiredQuizComponents', [])]
    from .replacement_learning import project_approved_learning
    components = project_approved_learning(components, metadata['replacement_settings'], metadata.get('progress_transfers', []))
    native['components'] = components[:len(native.get('components', []))]
    native['retiredQuizComponents'] = components[len(native['components']):]
    by_id = {}
    for course in courses:
        ref = str(course['source_course_ref'])
        by_id[f'legacy:{int(ref)}'] = dict(id=f'legacy:{int(ref)}', title=course['source_course_title'],
            source='legacy', moduleId=course.get('curriculum_module_ref'), _hours=round(hours[ref], 6), _items=legacy[ref])
    transfers = metadata.get('progress_transfers', [])
    approved = {f"current:{item['moduleId']}": item for item in transfers if item['status'] == 'approved'}
    links = {**metadata['imported_courses'], **metadata['replacement_courses']}
    uses = Counter(links.values())
    replaced = {ref for ref, old in links.items() if old in by_id and uses[old] == 1 and ref not in approved}
    skipped_titles = set()
    for module in current:
        ref = f"current:{module['id']}"
        if ref in replaced:
            skipped_titles.add(module['title'].strip().casefold())
            continue
        by_id.setdefault(ref, dict(id=ref, title=module['title'], source='current', moduleId=module['id'], _items=[]))
    activity_ids = defaultdict(set)
    attempted_components = {a['componentId'] for a in native.get('quizAttempts', []) if a.get('componentId')}
    attempted_quizzes = {str(a.get('quizId')) for a in native.get('quizAttempts', []) if not a.get('componentId')}
    for index, c in enumerate(components):
        ref = f"current:{c['moduleId']}" if c.get('moduleId') else f"unlinked:{c.get('module')}"
        if ref in replaced:
            skipped_titles.add(str(c.get('module') or '').strip().casefold())
            continue
        subject = by_id.setdefault(ref, dict(id=ref, title=c.get('module') or 'Unnamed subject', source='current', _items=[]))
        ident = c.get('componentId') or f"quiz:{(c.get('quizMeta') or {}).get('quizId') or str(c.get('week')) + ':' + str(index)}"
        if ident in activity_ids[ref]:
            continue
        activity_ids[ref].add(ident)
        if search is not None:
            subject['_items'].append(dict(id=ident,title=c.get('component') or '',component=c))
            continue
        placement = metadata['activity_dates'].get(ident) or {}
        if not placement or placement.get('date_source') in ('original_created_at', 'source_date', 'undated'):
            raw = c.get('sessionDate')
            placement = {'date': raw[:10] if raw else None}
            if raw:
                try:
                    at = date.fromisoformat(raw[:10])
                except ValueError:
                    placement = {'date':None,'month':'undated'}
                else:
                    monday = at-timedelta(days=at.weekday())
                    placement.update(month=raw[:7],week_start=monday.isoformat(),week_end=(monday+timedelta(days=6)).isoformat())
        subject['_items'].append(dict(id=ident, title=c.get('component') or '', completed=native_done(c, native) if search is None else False,
            position=index, week=c.get('week'), weekId=c.get('weekId'), schedule=placement, component=c,
            started=(c.get('componentId') in attempted_components or str((c.get('quizMeta') or {}).get('quizId')) in attempted_quizzes) if c.get('isQuiz') else False))
    titles = {s['title'].strip().casefold() for s in by_id.values()} | skipped_titles
    for title in native.get('modules', []):
        if title.strip().casefold() not in titles:
            by_id[f'unlinked:{title}'] = dict(id=f'unlinked:{title}', title=title, source='current', _items=[])
            titles.add(title.strip().casefold())
    module_list = list(modules.values())
    progress_index = module_record_index(records,module_list) if search is None else None
    for ref, transfer in approved.items():
        if ref in by_id:
            by_id.pop(transfer['courseRef'], None)
    with projection_stage('module_hours'):
        progress = supplement_module_hours(_canonical_module_progress(records,module_list,record_index=progress_index), records, credits) if search is None else {}
    today = datetime.now(UK).date().isoformat()
    seen = set()
    cards = []
    for subject in sorted(by_id.values(), key=lambda s: (s['title'].casefold(), s['id'])):
        items = []
        for item in subject.pop('_items'):
            c = item.get('component') or {}
            key = f"legacy:{item['group_id']}:{item['catalogue_kind']}:{item['activity_id']}" if 'group_id' in item else (
                f"component:{c['componentId']}" if c.get('componentId') else f"quiz:{c['quizMeta']['quizId']}" if (c.get('quizMeta') or {}).get('quizId') else None)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            items.append(item)
        if search is not None:
            cards.append({'id':subject['id'], '_matches':any(search in i['title'].lower() for i in items)})
            continue
        count, complete = len(items), sum(i['completed'] for i in items)
        module = modules.get(subject.get('moduleId'))
        imported = subject['source'] == 'legacy' and subject['id'] in metadata['imported_courses'].values()
        if imported:
            module = None
        group = metadata['previous_learning_groups'].get(subject['id'])
        if subject['source'] == 'legacy' and group in by_id and by_id[group]['source'] == 'current':
            subject['previousLearningFor'] = group
        if metadata['imported_courses'].get(subject['id']):
            subject['importedCourseRef'] = metadata['imported_courses'][subject['id']]
        subject.update(activityCount=count, completedActivityCount=complete, progress=percent(complete, count),
            status='Completed' if count and complete == count else 'In progress' if any(i['completed'] or i.get('started') for i in items) else 'Upcoming',
            nextTitle=next_title(items, today),
            finalTestPassed=any(i.get('component', {}).get('isQuiz') and 'final' in i['title'].lower() and native_done({k:v for k,v in i['component'].items() if k not in ('previousLearning','attendanceCompleted')}, native) for i in items),
            planModule={k: module.get(k) for k in ('start_date','end_date','tutor_name','total_otjh','educational_session_count','programme_name','cohort_name','group_name')} if module else None,
            actualHours=subject.pop('_hours',None) if subject['source'] == 'legacy' or subject.get('replacementCourseRef') else progress.get(subject.get('moduleId'), {}).get('hours', {}).get('actual'))
        demo = demo_credit(next((course for course in courses if subject['id'] == f"legacy:{course['source_course_ref']}"), None))
        if demo:
            subject.update(demo, status='In progress' if demo['progress'] else 'Upcoming')
        pending = next((item for item in transfers if item['courseRef'] == subject['id'] and item['status'] == 'pending'), None)
        if pending:
            subject.update(progress=pending['percent'], actualHours=pending['hours'],
                activityCount=pending['activityCount'], completedActivityCount=pending['completedActivityCount'], transferPending=True)
            if pending.get('demoProgress'):
                subject['demoProgress'] = True
        transfer = approved.get(subject['id'])
        if transfer:
            completed_ids = [item['id'] for item in items if item['completed']]
            subject['progress'] = merged_percent(completed_ids, count)
            subject['transferredProgress'] = transfer['percent']
            if transfer.get('demoProgress'):
                subject['demoProgress'] = True
            subject.pop('importedCourseRef', None)
            previous_ids = set(transfer['recordIds'])
            native_records = [record for record in records if str(record['id']) not in previous_ids]
            native_hours = supplement_module_hours(_canonical_module_progress(native_records, module_list), native_records, credits)
            subject['actualHours'] = round(transfer['hours'] + (native_hours.get(subject.get('moduleId'), {}).get('hours', {}).get('actual') or 0), 6)
            # Keep the component-derived status, including a started native
            # attempt with no completed components yet (0% is not "Upcoming").
        cards.append(subject)
    def totals(source):
        rows = [c for c in cards if c['source'] == source]
        total, complete = sum(c['activityCount'] for c in rows), sum(c['completedActivityCount'] for c in rows)
        weighted = sum(c['progress'] * c['activityCount'] for c in rows)
        return {'subjects': len(rows), 'total': total, 'completed': complete, 'percent': round(weighted/total, 2) if total else 0}
    if search is not None:
        return cards, {}
    return cards, {'current': totals('current'), 'previous': totals('legacy'),
                   'subjectCount': sum(not c.get('previousLearningFor') for c in cards)}


def load_context(source, owner, subject_id=None, *, purpose='catalogue', records_override=None, include_personal_replacements=False):
    """One request-local owner/assignment/ledger snapshot; no write-on-read path."""
    from .weekly_learning import build_learning_content_summary
    from .dashboard_tabs import install_request_caches
    from .student_activity import _builder_subject_metadata
    from .subject_origins import read_imported_courses, read_replacement_courses
    from .previous_learning_groups import read_previous_learning_groups
    from .builder_activity_dates import read_builder_activity_dates
    from .models import LearnerProfile
    from .teams_attendance import annotate_attendance_completion
    from .live_session_progress import read_live_credits, supplement_component_progress
    install_request_caches(source)
    source._canonical_profile = owner
    with projection_stage('profile'):
        profile = LearnerProfile.objects.only('id', 'enrolment_id', 'email').filter(pk=owner['id'], enrolment_id=source.pk).first()
    if profile is None:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
    source._case_file_profile = profile
    with projection_stage('assignments'):
        assigned = effective_training_plan(source, request_preset_cache(source))
    ids = list(dict.fromkeys(str(m['moduleId']) for m in assigned if m.get('moduleId')))
    record_options = dict(lineage_only=True,monthly_summary=True)
    if subject_id is None:
        record_options['catalogue_summary'] = True
    else:
        record_options['dashboard_metadata'] = True
    with projection_stage('ledger'):
        records = (records_override if records_override is not None else canonical.entries_for(owner, **record_options)) if purpose == 'catalogue' else []
    resolved, modules = None, None
    if subject_id is None and purpose == 'catalogue':
        resolved = {}
        with projection_stage('module_metadata'):
            with connections['enrolment'].cursor() as cur:
                modules = catalogue_module_rows(cur, ids, identity_metadata=resolved) if ids else {}
        resolved.setdefault('module_titles', {})
        resolved.setdefault('weeks', [])
        resolved.setdefault('covers', {})
    content_source, content_plan = source, assigned
    if subject_id is not None and not subject_id.startswith('activity:'):
        # A module link already identifies the content scope. Keep the full
        # assignment/mapping lists below for ownership and ambiguous successor
        # checks, but do not rebuild every other course just to open this one.
        # Activity-only links still need global identities to resolve their owner.
        selected_id = subject_id[8:] if subject_id.startswith('current:') else subject_id
        content_plan = [entry for entry in assigned if
            str(entry.get('moduleId') or '') == selected_id
            or (subject_id.startswith('unlinked:') and not entry.get('moduleId')
                and entry.get('moduleTitle') == subject_id[9:])]
        content_source = copy(source)
        content_source.training_plan = content_plan
        content_source.learning_plan = content_plan
    with projection_stage('native_learning'):
        native = build_learning_content_summary(content_source, source.pk, learning_summary=True, assigned_modules=content_plan,
            identities_only=True, preserve_legacy=True, strict=True, resolved_metadata=resolved)
    if subject_id is None and purpose == 'catalogue':
        with projection_stage('attendance_completion'):
            native['components'] = annotate_attendance_completion(native['components'], profile)
    credit_options = {'components':sorted(resolved['live_credit_components'],key=lambda c:c['componentId'])} if resolved is not None and 'live_credit_components' in resolved else {'module_ids':ids}
    with projection_stage('live_credits'):
        credits = read_live_credits(source, **credit_options) if subject_id is None and purpose == 'catalogue' else []
    native['componentProgress'] = supplement_component_progress(native['componentProgress'], credits)
    from .student_activity_access import student_activity_available
    with projection_stage('memberships'):
        if student_activity_available(source.aptem_id):
            courses = canonical.owned_source_courses(owner)
        else:
            from .progress_demo import courses_for
            courses = courses_for(owner)
    from .progress_transfer_store import read_state
    transfers = read_state(source.pk)['items'] if owner.get('progress_transfer_active') else []
    # Percentage-only approvals have no component lineage to revalidate. Keep
    # every identity for modules with matches so duplicate provenance still
    # prevents ambiguous completion, and retain pending/unapproved reads.
    approved_modules = {item['moduleId'] for item in transfers if item.get('status') == 'approved'}
    matched_modules = {item['moduleId'] for item in transfers
        if item.get('status') == 'approved' and item.get('matchedComponents')}
    with connections['enrolment'].cursor() as cur:
        if resolved is not None:
            covers = resolved['covers']
            builder = {f'current:{mid}': {'id':mid, 'title':modules[mid]['title']} for mid in resolved['module_titles']}
        elif purpose == 'search':
            cur.execute('''SELECT m.module_catalogue_id,m.title FROM curriculum.modules m
                WHERE (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent,'')<>'')
                  AND m.module_catalogue_id=ANY(%s)''',[ids])
            from .subject_content import clean_text
            covers, builder = {}, {f'current:{mid}':{'id':mid,'title':clean_text(title)} for mid,title in cur.fetchall()}
        else:
            covers, builder = _builder_subject_metadata(cur, [f'current:{mid}' for mid in ids])
        current = [builder[f'current:{mid}'] for mid in ids if f'current:{mid}' in builder]
        with projection_stage('subject_metadata'):
            imports = resolved['imported_courses'] if resolved is not None and 'imported_courses' in resolved else read_imported_courses(cur, current)
            # A personal transfer link must not hide the native module or merge
            # its hours before approval. Only transfer review opts into it.
            replacements = read_replacement_courses(cur, current, source.pk,
                **({'include_personal': True} if include_personal_replacements else {}))
            groups = read_previous_learning_groups(cur, source.pk, current) if purpose != 'search' else {}
        settings = {}
        # Both imported modules and verified successors can receive a transfer.
        # Approved lineage belongs to its saved destination even if the current
        # source-course mapping is later removed; keep checking that component's
        # identity against the same settings used by the activity reader.
        settings_modules = sorted((
            {key[8:] for key in {**imports, **replacements} if key[8:] not in approved_modules}
            | matched_modules
        ) & set(ids))
        settings_ids = list(dict.fromkeys(c['componentId']
            for c in [*native['components'], *native.get('retiredQuizComponents', [])]
            if c.get('componentId') and c.get('moduleId') in settings_modules))
        if settings_ids and purpose != 'search':
            with projection_stage('replacement_settings'):
                cur.execute('''SELECT id,jsonb_build_object('legacySettings',
                    CASE WHEN jsonb_typeof(settings_json->'legacySettings')='object' THEN
                        jsonb_build_object('sourceLessonId',settings_json->'legacySettings'->'sourceLessonId',
                                           'sourceMaterialKey',settings_json->'legacySettings'->'sourceMaterialKey')
                    ELSE settings_json->'legacySettings' END)
                    FROM curriculum.components WHERE module_catalogue_id=ANY(%s) AND id=ANY(%s)''',
                    [settings_modules, settings_ids])
                settings = dict(cur.fetchall())
        with projection_stage('activity_dates'):
            dates = read_builder_activity_dates(cur, ids, **({'resolved_metadata': resolved} if resolved is not None else {})) if subject_id is None and purpose != 'search' else {}
    selected_courses = courses if subject_id is None and purpose == 'catalogue' else []
    with projection_stage('historical_facts'):
        definitions, done, started, hours, record_ids = course_facts(owner, records, selected_courses)
    metadata = dict(covers=covers, builder_subjects=builder, current_subjects=current, activity_dates=dates,
        imported_courses=imports, replacement_courses=replacements, previous_learning_groups=groups,
        replacement_settings=settings, record_ids=record_ids, modules=modules)
    metadata['progress_transfers'] = transfers
    return assigned, ids, records, native, courses, definitions, done, started, hours, metadata, credits


def read_catalogue(source, kind, owner, *, upcoming_only=False):
    from .subject_store import covers_for
    assigned, ids, records, native, courses, definitions, done, started, hours, metadata, credits = load_context(source, owner)
    modules = metadata['modules']
    with projection_stage('card_aggregation'):
        cards, summary = assemble_cards(native, metadata['current_subjects'], courses, definitions, done, started, hours, metadata, records, modules, credits)
    if upcoming_only:
        # Reuse native completion before selecting deadlines, just as the
        # initial catalogue does. No cover or programme metric reads on expansion.
        return dict(upcoming=upcoming_links(source,kind,ids,native,metadata,{c['id'] for c in cards}))
    from .student_activity import _cover_url
    with projection_stage('covers'):
        covers = {**metadata['covers'], **{ref:_cover_url(path) for ref,path in covers_for([c['id'] for c in cards if c['source']=='legacy']).items()}}
    for card in cards:
        card['cover'] = covers.get(card['id']) or None
        # Native module identity is encoded by its stable navigation ID.
        if card['source'] == 'current':
            card.pop('moduleId', None)
    # Programme totals follow metrics(), not selected-week sums.
    with projection_stage('programme_hours'):
        planned = canonical.programme_planned_hours(source.pk, owner=owner) if journal_enabled() else None
        if not journal_enabled():
            targets = canonical.targets_for(owner)
            planned = sum(targets.values()) if targets else None
        actual = round(sum(canonical.recorded_seconds(r) for r in records if canonical.counts_as_actual(r))/3600, 4)
    with projection_stage('upcoming'):
        upcoming = upcoming_links(source, kind, ids, native, metadata, {c['id'] for c in cards}, limit=4)
    return dict(learner=dict(id=str(source.pk),kind=kind,name=owner['name'],programme=owner.get('programme') or '',cohort=str(source.cohort or ''),group=str(source.group or '')),
        summary=summary, modules=cards, otjh=dict(actual=actual,planned=planned),
        upcoming=upcoming[:3], hasMoreUpcoming=len(upcoming)>3)


def upcoming_links(source, kind, ids, native, metadata, visible, *, limit=None):
    """Navigation facts only: no attendance, join URLs or full session projection."""
    from .subject_content import clean_text
    from heapq import nsmallest
    from itertools import chain
    now = datetime.now(timezone.utc)
    today = now.astimezone(UK).date().isoformat()
    visible_current = {ref for ref in visible if ref.startswith('current:')}
    def assignment_links():
        for c in native['components']:
            if f"current:{c.get('moduleId')}" not in visible_current or c.get('type') not in ('assignment','checkpoint'):
                continue
            p = metadata['activity_dates'].get(c.get('componentId')) or {}
            if not native_done(c,native) and (p.get('due_timing') or '').lower()=='end of week' and (p.get('week_end') or '')>=today:
                yield dict(id=f"deadline:native:{c['componentId']}", title=c['component'], type=c['type'], date=p['week_end'],
                    href=f"/learner/component/{kind}/{source.pk}/{quote(c['componentId'],safe='')}", online=False)
    # Resolve shared delivery owners exactly as the canonical schedule reader.
    # Bound future rows in SQL and omit attendance/evidence and unused columns.
    sessions = canonical.query('''WITH RECURSIVE owners(module_id,owner_id,depth) AS (
        SELECT m.module_catalogue_id,coalesce(nullif(btrim(m.teams_shared_source_module_id),''),m.module_catalogue_id),0
        FROM curriculum.modules m WHERE m.module_catalogue_id=ANY(%s)
        UNION ALL SELECT owners.module_id,btrim(m.teams_shared_source_module_id),owners.depth+1
        FROM owners JOIN curriculum.modules m ON m.module_catalogue_id=owners.owner_id
        WHERE coalesce(btrim(m.teams_shared_source_module_id),'')<>'' AND owners.depth<5
    ), delivery AS (SELECT DISTINCT ON (module_id) module_id,owner_id FROM owners ORDER BY module_id,depth DESC)
    SELECT coalesce(o.id,s.id)::text AS id, s.id::text AS series_id,delivery.module_id,
        target.title,coalesce(o.scheduled_start,s.start_datetime) AS start,
        coalesce(nullif(o.join_url,''),nullif(s.join_url,'')) IS NOT NULL AS online
    FROM delivery JOIN curriculum.live_sessions s ON s.module_catalogue_id=delivery.owner_id
    JOIN curriculum.modules target ON target.module_catalogue_id=delivery.module_id
    LEFT JOIN curriculum.live_session_occurrences o ON o.live_session_id=s.id
    WHERE lower(s.status) NOT IN ('cancelled','deleted','failed','superseded')
      AND (o.id IS NULL OR lower(o.status) NOT IN ('cancelled','deleted','failed','superseded'))
      AND lower(coalesce(nullif(o.status,''),nullif(s.status,''),'scheduled')) NOT IN ('canceled','completed')
      AND (o.id IS NOT NULL OR s.repeat_pattern IS NULL OR s.repeat_pattern IN ('none',''))
      AND coalesce(o.scheduled_start,s.start_datetime)>=%s
    ORDER BY coalesce(o.scheduled_start,s.start_datetime),delivery.module_id,coalesce(o.id,s.id)
    LIMIT %s''', [ids,now.replace(tzinfo=None),limit]) if ids else []
    # Sessions need their existing action links, not activity or week catalogues.
    component_ids = defaultdict(list)
    for c in native['components']:
        if c.get('type') == 'live_session' and f"current:{c.get('moduleId')}" in visible_current:
            component_ids[c['moduleId']].append(c)
    session_links = []
    for session in sessions:
        start = session['start'].isoformat() if isinstance(session['start'], datetime) else str(session['start'])
        candidates = component_ids.get(session['module_id'],[])
        linked = [c for c in candidates if c.get('teamsLiveSessionId') in (session['id'],session['series_id'])]
        exact = [c for c in candidates if c.get('sessionDateTimeUtc') and session_instant(c['sessionDateTimeUtc'])==session_instant(start)]
        dated = [c for c in candidates if (metadata['activity_dates'].get(c['componentId']) or {}).get('date')==start[:10] or c.get('sessionDate')==start[:10]]
        matches = linked if len(linked)==1 else exact if len(exact)==1 else dated if len(dated)==1 else []
        href = f"/learner/my-learning/{kind}/{source.pk}?subject={quote('current:'+session['module_id'],safe='')}"
        if len(matches)==1:
            href=f"/learner/component/{kind}/{source.pk}/{quote(matches[0]['componentId'],safe='')}?week={quote(matches[0].get('week') or '',safe='')}"
        session_links.append(dict(id=f"session:{session['module_id']}:{session['id']}",title=clean_text(session['title']) or 'Live session',type='session',
            date=session_instant(start).isoformat(),href=href,online=session['online']))
    candidates = chain(assignment_links(),session_links)
    order = lambda item: (item['date'],item['id'])
    return nsmallest(limit,candidates,key=order) if limit else sorted(candidates,key=order)


def read_search(source, owner, term, offset):
    context = load_context(source, owner, purpose='search')
    _, _, records, native, courses, definitions, done, started, hours, metadata, credits = context
    # Validate the authoritative membership exclusions before applying them in
    # SQL. Return course identities, never a matching activity-title catalogue.
    canonical.learner_course_catalogue(courses,[])
    excluded = [str(c['source_course_ref'])+':'+ref for c in courses
        for ref in (canonical.decoded(c.get('verified_catalogue_scope')) or {}).get('excluded_activity_refs',[])]
    excluded.extend(str(c['source_course_ref'])+':'+ref for c in courses
        for ref in (c.get('demo_progress') or {}).get('excluded_activity_refs',[]))
    found = canonical.query('''SELECT DISTINCT c.source_course_ref
        FROM curriculum.source_activities a JOIN curriculum.source_courses c ON c.id=a.source_course_id
        WHERE a.source_course_id=ANY(%s) AND a.deleted_at IS NULL AND c.deleted_at IS NULL
          AND a.source_system='old_lms' AND position(lower(%s) in lower(a.source_activity_title))>0
          AND NOT ((c.source_course_ref::text || ':' || a.source_activity_id)=ANY(%s))
        ORDER BY c.source_course_ref''', [[c['id'] for c in courses],term,excluded]) if courses else []
    legacy_matches = {f"legacy:{int(row['source_course_ref'])}" for row in found}
    cards, _ = assemble_cards(native,metadata['current_subjects'],courses,definitions,done,started,hours,
        metadata,records,metadata['modules'] or {},credits,search=term.lower())
    matches = [dict(id=c['id']) for c in cards if c['_matches'] or c['id'] in legacy_matches]
    return dict(matches=matches[offset:offset+50], nextOffset=offset+50 if len(matches)>offset+50 else None)


def read_module(source, kind, owner, subject_id):
    from .weekly_learning import build_learning_content_summary
    from .student_activity_data import summarize_activities
    from .training_plan_dashboard import plan_sessions
    from .dashboard_training import module_rows
    assigned, ids, records, native, courses, definitions, done, started, hours, metadata, credits = load_context(source, owner, subject_id)
    owned = {f"legacy:{int(c['source_course_ref'])}":c for c in courses}
    valid = {f'current:{mid}' for mid in ids} | set(owned) | {f"unlinked:{entry['moduleTitle']}" for entry in assigned if not entry.get('moduleId') and entry.get('moduleTitle')}
    if subject_id and not subject_id.startswith(('current:', 'legacy:', 'unlinked:', 'activity:')) and subject_id in ids:
        subject_id = 'current:' + subject_id
    if subject_id.startswith('activity:'):
        target = subject_id[9:]
        native_matches = {f"current:{c['moduleId']}" if c.get('moduleId') else f"unlinked:{c.get('module')}"
            for c in [*native['components'], *native.get('retiredQuizComponents', [])] if c.get('componentId') == target}
        old_matches = set()
        if target.startswith(('catalogue:', 'la:')):
            bits = target.split(':')
            if len(bits) >= 3 and f'legacy:{bits[1]}' in owned:
                old_matches.add(f'legacy:{bits[1]}')
        elif target.startswith('record:'):
            for record in records:
                if str(record['id']) == target[7:]:
                    old_matches.update(f"legacy:{source['source_course_ref']}" for source in record.get('sources', [])
                        if source.get('source_system') == 'old_lms' and f"legacy:{source.get('source_course_ref')}" in owned)
        elif target.isdigit():
            matches = canonical.query('''SELECT DISTINCT c.source_course_ref FROM curriculum.source_activities a
                JOIN curriculum.source_courses c ON c.id=a.source_course_id
                WHERE a.source_course_id=ANY(%s) AND a.deleted_at IS NULL AND a.source_system='old_lms'
                  AND a.source_activity_id IN (%s,%s)''', [[c['id'] for c in courses], 'material:'+target, 'quiz:'+target])
            old_matches.update('legacy:'+str(row['source_course_ref']) for row in matches)
        matches = native_matches | old_matches
        if len(matches) != 1:
            raise ServiceError('This evidence activity is unavailable or ambiguous.', 'activity_not_found', 404)
        subject_id = next(iter(matches))
    if subject_id not in valid:
        raise ServiceError("Module not found in this learner's plan.", 'module_not_found', 404)
    links = {**metadata['imported_courses'], **metadata['replacement_courses']}
    transfers = metadata.get('progress_transfers', [])
    approved = {f"current:{item['moduleId']}": item for item in transfers if item['status'] == 'approved'}
    old = links.get(subject_id)
    if old in owned and Counter(links.values())[old] == 1 and subject_id not in approved:
        subject_id = old
    mid = subject_id[8:] if subject_id.startswith('current:') else owned.get(subject_id,{}).get('curriculum_module_ref')
    scope_ids = [mid] if mid in ids else []
    course_refs = [str(owned[subject_id]['source_course_ref'])] if subject_id.startswith('legacy:') else []
    activity = canonical.source_subjects(source.pk,summarize_activities,owner=owner,records=records,
        include_totals=False,course_ids=course_refs,module_ids=[]) if course_refs else None
    demo_course = owned.get(subject_id)
    if activity and (demo_course or {}).get('demo_progress'):
        excluded = set(demo_course['demo_progress']['excluded_activity_refs'])
        activity.update(summarize_activities([row for row in activity['activities']
            if f"{row.get('catalogue_kind')}:{row.get('source_activity_id')}" not in excluded]))
    selected = copy(source)
    native_scope_ids = scope_ids if subject_id.startswith('current:') else []
    chosen_plan = [entry for entry in assigned if (entry.get('moduleId') in native_scope_ids
        if mid else subject_id.startswith('unlinked:') and not entry.get('moduleId') and entry.get('moduleTitle')==subject_id[9:])]
    selected.training_plan = chosen_plan
    selected.learning_plan = chosen_plan
    progress = tuple(native.get(bucket,[]) for bucket in ('quizAttempts','videoProgress','componentProgress'))
    detail = build_learning_content_summary(selected,source.pk,learning_summary=True,assigned_modules=chosen_plan,
        progress=progress,strict=True,include_component_details=True)
    global_by_id = {c.get('componentId'):c for c in [*native['components'],*native.get('retiredQuizComponents',[])] if c.get('componentId')}
    detail['components'] = [{**{k:v for k,v in c.items() if k != 'previousLearning'},
                            **{k:global_by_id.get(c.get('componentId'),{}).get(k) for k in ('attendanceCompleted',) if k in global_by_id.get(c.get('componentId'),{})}}
                            for c in detail['components'] if (c.get('moduleId') in native_scope_ids if mid else c.get('module')==subject_id[9:])]
    detail['retiredQuizComponents'] = [{k:v for k,v in c.items() if k != 'previousLearning'} for c in native.get('retiredQuizComponents',[]) if c.get('moduleId') in native_scope_ids]
    from .replacement_learning import project_approved_learning
    detail['components'] = project_approved_learning(detail['components'], metadata['replacement_settings'], transfers)
    component_set = {c.get('componentId') for c in [*detail['components'],*detail['retiredQuizComponents']]}
    quiz_ids = {str((c.get('quizMeta') or {}).get('quizId')) for c in detail['components'] if c.get('isQuiz')}
    detail['quizAttempts'] = [a for a in native['quizAttempts'] if a.get('componentId') in component_set if a.get('componentId')] + [a for a in native['quizAttempts'] if not a.get('componentId') and str(a.get('quizId')) in quiz_ids]
    for bucket in ('videoProgress','componentProgress'):
        detail[bucket]=[r for r in native[bucket] if r.get('componentId') in component_set]
    from .teams_attendance import annotate_attendance_completion
    from .live_session_progress import read_live_credits, supplement_component_progress
    detail['components'] = annotate_attendance_completion(detail['components'], source._case_file_profile)
    credits = read_live_credits(source, module_ids=native_scope_ids)
    detail['componentProgress'] = supplement_component_progress(detail['componentProgress'], credits)
    detail['name']=owner['name']
    detail['modules']=[subject_id[9:]] if subject_id.startswith('unlinked:') else list(dict.fromkeys(c.get('module') for c in detail['components'] if c.get('module')))
    with connections['enrolment'].cursor() as cur:
        modules=module_rows(cur,scope_ids,rich_id=mid) if scope_ids else {}
        sessions=plan_sessions(cur,str(source.email or '').strip().casefold(),scope_ids) if scope_ids else []
    visible_refs = {subject_id, *(f'current:{value}' for value in scope_ids), *(f'legacy:{value}' for value in course_refs)}
    clean_metadata={k:{ref:value for ref,value in metadata[k].items() if ref in visible_refs}
        for k in ('covers','builder_subjects','imported_courses','replacement_courses','previous_learning_groups')}
    clean_metadata['current_subjects']=[m for m in metadata['current_subjects'] if m['id'] in native_scope_ids]
    if transfers:
        clean_metadata['approved_transfers'] = {ref: item['courseRef'] for ref,item in approved.items() if ref in visible_refs}
        clean_metadata['module_credits'] = {}
    transfer = approved.get(subject_id)
    if transfer:
        from .progress_transfer_policy import merged_percent
        from .training_plan_dashboard import _canonical_module_progress
        from .live_session_progress import supplement_module_hours
        # Same stable identities as the catalogue; duplicate placements must not
        # inflate the denominator or count a completed component twice.
        components = {}
        for index, c in enumerate([*detail['components'], *detail['retiredQuizComponents']]):
            ident = c.get('componentId') or f"quiz:{(c.get('quizMeta') or {}).get('quizId') or str(c.get('week')) + ':' + str(index)}"
            components.setdefault(ident, c)
        component_ids = {ident for ident, c in components.items() if native_done(c, detail)}
        previous_ids = set(transfer['recordIds'])
        native_records = [record for record in records if str(record['id']) not in previous_ids]
        native_hours = supplement_module_hours(_canonical_module_progress(native_records, list(modules.values())), native_records, credits)
        clean_metadata['module_credits'][subject_id] = {
            'progress': merged_percent(component_ids, len(components)), 'transferredProgress': transfer['percent'],
            'actualHours': round(transfer['hours'] + (native_hours.get(mid, {}).get('hours', {}).get('actual') or 0), 6)}
        if transfer.get('demoProgress'):
            clean_metadata['module_credits'][subject_id]['demoProgress'] = True
    from .progress_demo import credit as demo_credit
    demo = demo_credit(demo_course)
    if demo:
        clean_metadata.setdefault('module_credits', {})[subject_id] = demo
    pending = next((item for item in transfers if item['courseRef'] == subject_id), None)
    if pending:
        clean_metadata['module_credits'][subject_id] = {'progress': pending['percent'], 'actualHours': pending['hours'], 'transferPending': pending['status'] == 'pending'}
        if pending.get('demoProgress'):
            clean_metadata['module_credits'][subject_id]['demoProgress'] = True
    from .builder_activity_dates import read_builder_activity_dates
    with connections['enrolment'].cursor() as cur:
        dates = read_builder_activity_dates(cur, scope_ids)
    clean_metadata['activity_dates']={key:value for key,value in dates.items() if key in component_set}
    from .subject_store import covers_for
    from .student_activity import _cover_url
    clean_metadata['covers'].update({ref:_cover_url(path) for ref,path in covers_for([f'legacy:{ref}' for ref in course_refs]).items()})
    if activity:
        activity['verification_status']='verified'
        activity['replacement_hours']={}
    # native_done caches sets while calculating transferred progress. They are
    # request-local lookup indexes, not part of the learner detail JSON contract.
    detail.pop('_completion', None)
    return dict(subjectId=subject_id,detail=detail,activity=activity,metadata=clean_metadata,
        schedule=dict(modules=list(modules.values()),moduleLinks=clean_metadata['builder_subjects'],generatedAt=datetime.now(timezone.utc).isoformat()),sessions=sessions)


@require_GET
@local_catalogue_trace
@learner_self_or_staff(kwarg='pk')
@learner_journal_view
def my_learning(request,kind,pk,subject_id=None,resource=None):
    model=SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error':'Learner not found.'},status=404)
    try:
        with projection_stage('source'):
            source=model.all_learners.only(*SOURCE_FIELDS).get(pk=pk)
        with projection_stage('owner'):
            owner=canonical.require_profile(pk)
        if resource == 'search':
            term = request.GET.get('q','').strip()
            try:
                offset = int(request.GET.get('offset',0))
            except ValueError:
                return JsonResponse({'error':'Invalid search offset.'},status=400)
            if len(term)>200 or offset<0:
                return JsonResponse({'error':'Invalid search.'},status=400)
            payload = read_search(source,owner,term,offset) if term else dict(matches=[],nextOffset=None)
        elif resource == 'upcoming':
            payload = read_catalogue(source,kind,owner,upcoming_only=True)
        else:
            payload=read_module(source,kind,owner,subject_id) if subject_id is not None else read_catalogue(source,kind,owner)
    except model.DoesNotExist:
        return JsonResponse({'error':'Learner not found.'},status=404)
    except ServiceError as error:
        return JsonResponse({'error':str(error)},status=error.status)
    except DatabaseError:
        return JsonResponse({'error':'Could not load your learning. Please try again.'},status=503)
    with projection_stage('serialize'):
        response=JsonResponse(payload)
    response['Cache-Control']='private, no-store'
    return response
