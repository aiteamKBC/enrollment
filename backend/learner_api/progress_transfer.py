"""Self-service requests and admin-only approval of module-level credit."""
import functools
import json
from uuid import uuid4

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from login.permissions import authenticate_request, _unauthenticated
from old_otjh.service import ServiceError

from . import canonical_learning as canonical
from . import progress_transfer_store as store
from .journal_sources import learner_journal_reads
from .progress_transfer_policy import eligible_pairs, public_item, record_courses, is_old_record
from .progress_transfer_matching import build_review, module_review, validate_decisions, manual_completion, component_counts


def endpoint(role):
    def decorate(view):
        @csrf_protect
        @require_http_methods(['GET', 'POST'])
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                account = authenticate_request(request)
                if account is None:
                    return _unauthenticated(request)
                if account.role != role:
                    return JsonResponse({'error': 'You do not have permission to perform this action.'}, status=403)
                if role == 'learner' and int(kwargs['pk']) != account.subject_id:
                    return JsonResponse({'error': 'Learner not found.'}, status=404)
                if request.method == 'POST':
                    payload = json.loads(request.body or b'{}')
                    allowed = {'confirm', 'reviewToken', 'mappings'} if role == 'admin' and kwargs.get('request_id') else {'confirm'}
                    if not isinstance(payload, dict) or payload.get('confirm') is not True or set(payload) - allowed:
                        return JsonResponse({'error': 'Confirm this action to continue.'}, status=400)
                response = view(request, account, *args, **kwargs)
            except ServiceError as exc:
                response = JsonResponse({'error': str(exc), 'code': exc.code}, status=exc.status)
            except (ValueError, TypeError, UnicodeDecodeError):
                response = JsonResponse({'error': 'Invalid transfer request.'}, status=400)
            except DatabaseError:
                response = JsonResponse({'error': 'Progress transfer is unavailable. Please try again.'}, status=503)
            response['Cache-Control'] = 'private, no-store'
            return response
        return wrapped
    return decorate


def source_owner(kind, pk):
    from .my_learning import SOURCE_FIELDS, SOURCE_MODELS
    model = SOURCE_MODELS.get(kind)
    source = model.all_learners.only(*SOURCE_FIELDS).filter(pk=pk).first() if model else None
    if source is None:
        raise ServiceError('Learner not found.', 'not_found', 404)
    source_kind = 'commercial' if source.learner_type == 'commercial' else 'apprenticeship'
    if kind != source_kind:
        raise ServiceError('Learner not found.', 'not_found', 404)
    return source, canonical.require_profile(pk)


def available(source, owner, state, *, records=None):
    from .my_learning import load_context
    context = load_context(source, owner, include_personal_replacements=True,
                           **({'records_override': records} if records is not None else {}))
    _, assigned_ids, _, _, courses, _, _, _, _, metadata, _ = context
    pairs = eligible_pairs(metadata['imported_courses'], metadata['replacement_courses'],
        {f"legacy:{int(course['source_course_ref'])}" for course in courses},
        {item['id'] for item in metadata['current_subjects']} & set(assigned_ids), state['items'])
    return context, pairs


def request_summary(items):
    grouped = {}
    for item in items:
        row = grouped.setdefault(item['requestId'], {key: item[key] for key in ('requestId', 'status', 'requestedAt', 'approvedAt')})
        row.setdefault('modules', []).append(public_item(item))
    return list(grouped.values())


@learner_journal_reads()
def transfer_preview(source, owner):
    state = store.read_state(source.pk)
    context, pairs = available(source, owner, state)
    courses, metadata = context[4], context[9]
    titles = {f"legacy:{int(course['source_course_ref'])}": course['source_course_title'] for course in courses}
    current = {f"current:{item['id']}": item['title'] for item in metadata['current_subjects']}
    demos = {f"legacy:{course['source_course_ref']}" for course in courses if course.get('demo_progress')}
    return {'eligible': [{'courseRef': old, 'moduleId': ref[8:], 'oldTitle': titles[old], 'newTitle': current[ref],
                         **({'demoProgress': True} if old in demos else {})}
                         for ref, old in pairs.items()], 'requests': request_summary(state['items'])}


@learner_journal_reads()
def create_request(source, kind, owner, account, *, module_ids=None):
    """Freeze eligible progress; internal callers may select an explicit subset."""
    from .my_learning import assemble_cards
    selected = None if module_ids is None else set(module_ids)
    if selected is not None and (not selected or any(not isinstance(mid, str) or not mid for mid in selected)):
        raise ServiceError('Choose eligible modules for this request.', 'invalid_transfer_modules', 400)
    with store.lock_learner(source.pk) as cursor:
        state = store.read_state(source.pk)
        pending = [item for item in state['items'] if item['status'] == 'pending']
        if pending:
            if selected is not None and selected != {item['moduleId'] for item in pending}:
                raise ServiceError('An existing pending request covers different modules.', 'transfer_already_pending', 409)
            return request_summary(pending)[0]
        # One immutable read supplies both the numeric preview and the saved
        # ledger facts, even if an external import arrives during this request.
        snapshot_records = canonical.entries_for(owner)
        context, pairs = available(source, owner, state, records=snapshot_records)
        if selected is not None:
            if not selected.issubset({ref[8:] for ref in pairs}):
                raise ServiceError('A selected module is not eligible for transfer.', 'invalid_transfer_modules', 409)
            pairs = {ref: old for ref, old in pairs.items() if ref[8:] in selected}
        if not pairs:
            raise ServiceError('No old modules are currently eligible for transfer.', 'no_eligible_modules', 409)
        _, _, records, native, courses, definitions, done, started, hours, metadata, credits = context
        cards, _ = assemble_cards(native, metadata['current_subjects'], courses, definitions, done, started,
                                 hours, metadata, records, metadata['modules'], credits)
        by_id = {card['id']: card for card in cards}
        request_id = str(uuid4())
        items, frozen_ids = [], set()
        current_titles = {row['id']: row['title'] for row in metadata['current_subjects']}
        for ref, old in pairs.items():
            card = by_id[old]
            ids = {str(ident) for (course, _), values in metadata['record_ids'].items()
                   if str(course) == old[7:] for ident in values}
            frozen_ids.update(ids)
            items.append(dict(courseRef=old, moduleId=ref[8:], oldTitle=card['title'], newTitle=current_titles[ref[8:]],
                percent=card['progress'], hours=card['actualHours'] or 0,
                activityCount=card['activityCount'], completedActivityCount=card['completedActivityCount'],
                recordIds=sorted(ids)))
            if card.get('demoProgress'):
                items[-1]['demoProgress'] = True
            # Aggregate demo credit never invents completed source activities.
            items[-1]['completedSourceActivities'] = sorted(ref for course, ref in done if str(course) == old[7:])
        # Load complete records once, retaining evidence and reporting history.
        # Snapshot only historical rows; native activity saves remain independent.
        frozen_courses = {old[7:] for old in pairs.values()}
        snapshots = [record for record in snapshot_records if is_old_record(record)
                     and (str(record['id']) in frozen_ids or record_courses(record) & frozen_courses)]
        cursor.execute(f'''INSERT INTO {store.REQUESTS}
            (id,enrolment_id,learner_id,learner_kind,requested_by,records) VALUES (%s,%s,%s,%s,%s,%s::jsonb)''',
            [request_id, source.pk, owner['id'], kind, account.id, store.dumps(snapshots)])
        for item in items:
            cursor.execute(f'''INSERT INTO {store.ITEMS} (request_id,enrolment_id,course_ref,module_id,snapshot)
                VALUES (%s,%s,%s,%s,%s::jsonb)''', [request_id, source.pk, item['courseRef'], item['moduleId'], store.dumps(item)])
        return next(row for row in request_summary(store.read_state(source.pk)['items']) if row['requestId'] == request_id)


@learner_journal_reads()
def review_request(request_id):
    from .my_learning import load_context
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'SELECT enrolment_id,learner_kind FROM {store.REQUESTS} WHERE id=%s', [request_id])
        row = cursor.fetchone()
    if not row:
        raise ServiceError('Transfer request not found.', 'not_found', 404)
    pk, kind = row
    source, owner = source_owner(kind, pk)
    items = [item for item in store.read_state(pk)['items'] if item['requestId'] == str(request_id)]
    if not items or any(item['status'] != 'pending' for item in items):
        raise ServiceError('This request is no longer pending. Refresh the requests.', 'transfer_not_pending', 409)
    from .progress_transfer_content import verified_source_types
    context = with_source_weeks(load_context(source, owner, include_personal_replacements=True), items)
    context = verified_source_types(with_planned_hours(context, items), items)
    # Review suggestions use the names in this review snapshot. Loading old
    # documents/question banks is not needed to match completed components.
    return build_review(request_id, items, context)


def with_source_weeks(context, items):
    """Recover missing old weeks from owned source sections, only for this transfer."""
    from .course_catalogue_display import catalogue_display_context, read_export_placements
    completed = {(item['courseRef'][7:], ref) for item in items
                 for ref in item.get('completedSourceActivities',
                     [ref for course, ref in context[6] if str(course) == item['courseRef'][7:]])}
    missing = {(str(d['source_course_ref']), d['source_activity_id']) for d in context[5]
               if (str(d['source_course_ref']), d['source_activity_id']) in completed
               and not (d.get('source_section_title') or '').strip()}
    missing_courses = {course for course, _ in missing}
    courses = [course for course in context[4] if str(course['source_course_ref']) in missing_courses]
    if not courses:
        return context
    course_refs = {str(course['source_course_ref']) for course in courses}
    # Include unfinished parents/siblings as section evidence; only frozen
    # completed references are returned by module_review or receive credit.
    definitions = [d for d in context[5] if str(d['source_course_ref']) in course_refs]
    enriched = catalogue_display_context(definitions, read_export_placements(canonical.query, courses))
    weeks = {}
    for definition in enriched:
        section_title = (definition.get('source_section') or {}).get('title')
        if not section_title and definition['_display_schedule']['date_source'] == 'section_needs_review':
            continue
        weeks[(str(definition['source_course_ref']), definition['source_activity_id'])] = (
            section_title or definition.get('source_section_title') or '')
    result = list(context)
    result[5] = []
    for definition in context[5]:
        key = (str(definition['source_course_ref']), definition['source_activity_id'])
        result[5].append({**definition, 'source_section_title': weeks[key]}
                         if key in missing and key in weeks else definition)
    return tuple(result)


def with_planned_hours(context, items):
    """Fetch authored hours only for request targets; catalogue identities omit them."""
    module_ids = {item['moduleId'] for item in items}
    ids = sorted({c['componentId'] for c in context[3]['components']
                  if c.get('componentId') and c.get('moduleId') in module_ids and 'expectedOtjh' not in c})
    if not ids:
        return context
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT id,module_catalogue_id,expected_otjh FROM curriculum.components
            WHERE id=ANY(%s) AND module_catalogue_id=ANY(%s)''', [ids, sorted(module_ids)])
        hours = {(mid, ident): float(value) if value is not None else None for ident, mid, value in cursor.fetchall()}
    result = list(context)
    result[3] = {**context[3], 'components': [
        {**c, 'expectedOtjh': hours.get((c.get('moduleId'), c.get('componentId')))} if c.get('componentId') in ids else c
        for c in context[3]['components']]}
    return tuple(result)


@learner_journal_reads()
def approve_request(request_id, account, decisions=None):
    from .my_learning import load_context, native_done
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'SELECT enrolment_id,learner_kind FROM {store.REQUESTS} WHERE id=%s', [request_id])
        row = cursor.fetchone()
    if not row:
        raise ServiceError('Transfer request not found.', 'not_found', 404)
    pk, kind = row
    with store.lock_learner(pk) as cursor:
        state = store.read_state(pk)
        items = [item for item in state['items'] if item['requestId'] == str(request_id)]
        if not items:
            raise ServiceError('Transfer request not found.', 'not_found', 404)
        if all(item['status'] == 'approved' for item in items):
            return request_summary(items)[0]
        source, owner = source_owner(kind, pk)
        context = load_context(source, owner, include_personal_replacements=True)
        _, assigned, _, native, courses, _, done, _, _, metadata, _ = context
        pairs = eligible_pairs(metadata['imported_courses'], metadata['replacement_courses'],
            {f"legacy:{int(course['source_course_ref'])}" for course in courses},
            {row['id'] for row in metadata['current_subjects']} & set(assigned))
        if any(pairs.get('current:' + item['moduleId']) != item['courseRef'] for item in items):
            raise ServiceError('A module assignment or course mapping changed. Review it before approving this request.',
                               'transfer_mapping_changed', 409)
        from .progress_transfer_content import verified_source_types
        context = with_source_weeks(context, items)
        context = verified_source_types(with_planned_hours(context, items), items)
        review = build_review(request_id, items, context)
        manual = validate_decisions(review, decisions)
        for item in items:
            module, matches, components = module_review(item, context)
            item['completedSourceActivities'] = [source['sourceActivityRef'] for source in module['sources']]
            item['baselineCompletedIds'] = [c['componentId'] for c in components if c.get('componentId') and native_done(c, native)]
            item['baselinePercent'] = 100 * len(set(item['baselineCompletedIds'])) / len(components) if components else 0
            item['componentMatches'] = []
            for source_component in module['sources']:
                ref = source_component['sourceActivityRef']
                target = manual.get((item['moduleId'], ref))
                if target:
                    matches[target['componentId']] = manual_completion(item, ref, target)
                else:
                    target = next(t for t in module['targets'] if t['componentId'] == source_component['automaticTargetId'])
                item['componentMatches'].append({'sourceActivityRef': ref, 'sourceTitle': source_component['title'],
                    'targetComponentId': target['componentId'], 'targetTitle': target['title'],
                    'manual': not bool(source_component['automaticTargetId']),
                    **({'plannedHours': target['plannedHours']} if not source_component['automaticTargetId'] else {})})
            item['matchedComponents'] = matches
            item['componentCounts'] = component_counts(module, matches)
            counts = item['componentCounts']
            item['mergedPercent'] = round(100 * counts['completed'] / counts['total'], 2) if counts['total'] else 0
            cursor.execute(f'UPDATE {store.ITEMS} SET snapshot=%s::jsonb WHERE request_id=%s AND enrolment_id=%s AND module_id=%s',
                [store.dumps({key: value for key, value in item.items() if key not in ('requestId','status','requestedAt','approvedAt')}), request_id, pk, item['moduleId']])
        cursor.execute(f"UPDATE {store.REQUESTS} SET status='approved',approved_by=%s,approved_at=now() WHERE id=%s AND status='pending'",
                       [account.id, request_id])
        return next(row for row in request_summary(store.read_state(pk)['items']) if row['requestId'] == str(request_id))


@learner_journal_reads()
def cover_transfer_metadata(pk):
    """Keep existing subject-panel consumers consistent with My Learning cards."""
    state = store.read_state(pk)
    if not state['items']:
        return {}
    from .my_learning import load_context, assemble_cards
    kind = 'commercial' if canonical.require_profile(pk)['learner_type'] == 'commercial' else 'apprenticeship'
    source, owner = source_owner(kind, pk)
    context = load_context(source, owner)
    _, _, records, native, courses, definitions, done, started, hours, metadata, credits = context
    cards, _ = assemble_cards(native, metadata['current_subjects'], courses, definitions, done, started,
        hours, metadata, records, metadata['modules'], credits)
    return {
        'approved_transfers': {f"current:{item['moduleId']}": item['courseRef'] for item in state['items'] if item['status'] == 'approved'},
        'module_credits': {card['id']: {'progress': card['progress'], 'actualHours': card['actualHours'],
            **{key:card[key] for key in ('transferredProgress','transferPending','demoProgress') if key in card}}
            for card in cards if 'transferredProgress' in card or card.get('transferPending')},
    }


@endpoint('learner')
def learner_request(request, account, kind, pk):
    source, owner = source_owner(kind, pk)
    if request.method == 'POST':
        return JsonResponse({'request': create_request(source, kind, owner, account)})
    return JsonResponse({**transfer_preview(source, owner), 'csrfToken': get_token(request)})


@endpoint('admin')
def admin_requests(request, account, request_id=None):
    if request_id is not None:
        if request.method == 'GET':
            return JsonResponse({**review_request(request_id), 'csrfToken': get_token(request)})
        payload = json.loads(request.body or b'{}')
        decisions = {key: payload[key] for key in ('reviewToken', 'mappings') if key in payload} or None
        return JsonResponse({'request': approve_request(request_id, account, decisions)})
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    offset = int(request.GET.get('offset', 0))
    if offset < 0:
        raise ValueError('Invalid offset')
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'''SELECT r.id,r.enrolment_id,r.learner_kind,r.status,r.requested_at,r.approved_at,
            l.full_name,l.programme,jsonb_agg(i.snapshot ORDER BY i.course_ref)
            FROM {store.REQUESTS} r JOIN "Learner".learners l ON l.id=r.learner_id AND l.enrolment_id=r.enrolment_id
            JOIN {store.ITEMS} i ON i.request_id=r.id AND i.enrolment_id=r.enrolment_id
            GROUP BY r.id,l.full_name,l.programme
            ORDER BY (r.status='pending') DESC,r.requested_at DESC,r.id LIMIT 51 OFFSET %s''', [offset])
        rows = cursor.fetchall()
    result = [{'requestId': str(row[0]), 'learnerId': str(row[1]), 'learnerKind': row[2], 'status': row[3],
               'requestedAt': row[4].isoformat(), 'approvedAt': row[5].isoformat() if row[5] else None,
               'learnerName': row[6], 'programme': row[7], 'modules': [public_item(item) for item in store.decode(row[8])]}
              for row in rows[:50]]
    return JsonResponse({'requests': result, 'nextOffset': offset+50 if len(rows)>50 else None, 'csrfToken': get_token(request)})
