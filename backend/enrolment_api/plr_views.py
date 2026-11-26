"""PLR access uses the existing learner ownership gate and enrolment staff grants."""
import json
from django.db import DatabaseError, IntegrityError
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from .auth import enrolment_login_required
from .plr_pdf import MAX_BYTES, PlrError, parse_bounded
from . import plr_service as service


@enrolment_login_required
@csrf_protect
def personal_learning_records(request, kind, learner_id):
    from .extended_ilr import KINDS
    from curriculum_api.versioning import audit_context
    model = KINDS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    # This is deliberately independent of the dev auth bypass switch.
    if request.method != 'GET' and not service.can_manage(request):
        return JsonResponse({'error': 'Enrolment administrator access is required.'}, status=403)
    try:
        learner = model.objects.filter(pk=learner_id).first()
        if learner is None:
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        if request.method == 'GET':
            return JsonResponse({**service.state(kind, learner_id), 'canManage': service.can_manage(request), 'csrfToken': get_token(request)})
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return JsonResponse({'error': 'Method not allowed.'}, status=405)
        if request.method == 'POST':
            file = request.FILES.get('file')
            if not file or file.size > MAX_BYTES or not file.name.lower().endswith('.pdf') or file.content_type not in ('application/pdf', 'application/octet-stream'):
                raise PlrError('Select a PDF file no larger than 8 MB.')
            try:
                data = file.read(MAX_BYTES + 1)
            finally:
                file.close()
            report = parse_bounded(data)
            entered = request.POST.get('enteredUln', '')
            service.valid_uln(entered)
            with audit_context(reason='PLR PDF import', metadata={'learner_id': learner_id}):
                result = service.import_report(kind, learner, report, entered, request.POST.get('identityReviewed') == 'true')
            if not result['added']:
                message = 'No new records found. All records have already been imported.' if result['skipped'] else 'PLR imported successfully. The report contains no records.'
            else:
                message = f"PLR imported successfully. {result['added']} records added and {result['skipped']} duplicate records skipped."
            return JsonResponse({**result, 'message': message})
        if len(request.body) > 100000:
            raise PlrError('The PLR request is too large.')
        try:
            body = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise PlrError('Provide a valid JSON request.') from None
        if not isinstance(body, dict):
            raise PlrError('Provide a valid JSON object.')
        with audit_context(reason='PLR ULN update' if request.method == 'PUT' else 'PLR record deletion' if request.method == 'DELETE' else 'PLR record edit', metadata={'learner_id': learner_id}):
            if request.method == 'PUT':
                result = service.save_uln(kind, learner_id, body.get('uln'), body.get('revision'))
            else:
                result = service.change_record(kind, learner_id, body.get('id'), body.get('record'), body.get('revision'), delete=request.method == 'DELETE')
        return JsonResponse(result)
    except PlrError as exc:
        return JsonResponse({'error': str(exc), 'code': exc.code}, status=exc.status)
    except IntegrityError:
        return JsonResponse({'error': 'The PLR changed or the ULN is already assigned. Refresh before retrying.'}, status=409)
    except DatabaseError:
        # Database exception strings may include identifiers: never log them here.
        return JsonResponse({'error': 'PLR storage is unavailable. No changes were saved.'}, status=503)
