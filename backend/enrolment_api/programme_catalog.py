import json
import logging
import re
from decimal import Decimal
from functools import partial, wraps

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import ValidationError
from rest_framework.fields import UUIDField

from enrolment_api.auth import enrolment_login_required
from login.identity import accesses_for_staff
from programme_suitability.catalog_models import CatalogDelivery, CatalogGroup, CatalogModuleRevision, CatalogProgramme
from programme_suitability.catalog_pricing import price_programme, written_agreement_costs
from programme_suitability.catalog_readiness import catalogue_schema_ready
from programme_suitability.catalog_serializers import (
    CohortSerializer, DeliverySerializer, GroupSerializer, KsbSerializer, ModuleSerializer,
    ProgrammeListSerializer, ProgrammeSerializer,
)
from programme_suitability.catalog_services import staff_members
from programme_suitability.permissions import is_staff


RESOURCES = {
    'cohorts': CohortSerializer,
    'groups': GroupSerializer,
    'deliveries': DeliverySerializer,
    'modules': ModuleSerializer,
    'ksbs': KsbSerializer,
}


logger = logging.getLogger(__name__)


class CatalogError(Exception):
    def __init__(self, message, status, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def catalog_access(view=None, *, requires_schema=True):
    if view is None:
        return partial(catalog_access, requires_schema=requires_schema)

    @never_cache
    @enrolment_login_required
    @csrf_protect
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not is_staff(request):
            return JsonResponse({'error': 'Admissions staff access required.'}, status=403)
        try:
            if requires_schema and not catalogue_schema_ready():
                raise CatalogError('The Programme Catalogue database setup is required.',
                                   503, code='catalog_schema_not_ready')
            return view(request, *args, **kwargs)
        except CatalogError as exc:
            return JsonResponse({'error': str(exc), **exc.details}, status=exc.status)
        except ValidationError as exc:
            return JsonResponse({'error': 'Check the submitted fields.', 'fields': exc.detail}, status=400)
        except IntegrityError:
            return JsonResponse({'error': 'The change conflicts with an existing record. Refresh and try again.'}, status=409)
        except DatabaseError:
            logger.exception('Programme catalogue database request failed.')
            return JsonResponse({'error': 'The catalogue database request failed. Try again.',
                                 'code': 'catalog_database_error'}, status=503)
    return guarded


@catalog_access(requires_schema=False)
@require_http_methods(['GET'])
def catalogue_readiness(request):
    return JsonResponse({'state': 'ready' if catalogue_schema_ready() else 'schema_not_ready'})


def body(request):
    def invalid_constant(_value):
        raise ValueError

    try:
        data = json.loads(request.body, parse_float=Decimal, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise CatalogError('Request body must be valid JSON.', 400) from exc
    if not isinstance(data, dict):
        raise CatalogError('Request body must be a JSON object.', 400)
    return data


def find(queryset, pk):
    record = queryset.filter(pk=pk).first()
    if record is None:
        raise CatalogError('Record not found in this programme catalogue.', 404)
    return record


def check_revision(request, record):
    expected = request.headers.get('If-Match')
    if expected is None:
        raise CatalogError('Reload this record before changing it.', 428, code='catalog_revision_required')
    match = re.fullmatch(r'"([1-9][0-9]{0,18})"', expected)
    if match is None:
        raise CatalogError('If-Match must contain a quoted record revision.', 400)
    if int(match.group(1)) != record.revision:
        raise CatalogError('Another change was saved after you opened this record. Reload the latest version before trying again.',
                           409, code='catalog_revision_conflict', current_revision=record.revision)


def save(serializer_class, data, instance=None, *, programme=None, parent_fields=None):
    serializer = serializer_class(
        instance, data=data, partial=instance is not None, context={'programme': programme},
    )
    serializer.is_valid(raise_exception=True)
    fields = dict(parent_fields or {})
    if instance is not None:
        fields['revision'] = instance.revision + 1
    serializer.save(**fields)
    return JsonResponse(serializer.data, status=200 if instance is not None else 201)


def import_ksbs(data, programme):
    if data.keys() != {'ksbs'}:
        raise ValidationError({'ksbs': 'Submit only the KSB rows for a bulk import.'})
    serializer = KsbSerializer(data=data['ksbs'], many=True, allow_empty=False, context={'programme': programme})
    serializer.is_valid(raise_exception=True)
    codes = [row['code'] for row in serializer.validated_data]
    if len(codes) != len(set(codes)):
        raise ValidationError({'ksbs': 'Each imported KSB must have a distinct code.'})
    serializer.save(programme=programme)
    return JsonResponse({'ksbs': serializer.data}, status=201)


def archive(record, **changes):
    if not record.archived_at:
        record.archived_at = timezone.now()
        record.revision += 1
        for field, value in changes.items():
            setattr(record, field, value)
        record.save(update_fields=['archived_at', 'updated_at', 'revision', *changes])
    return HttpResponse(status=204)


def require_current(*records):
    if any(record.archived_at for record in records):
        raise CatalogError('Archived records and their descendants cannot be edited.', 409)


def require_manual(*records):
    if any(record.source_system != 'manual' for record in records):
        raise CatalogError('Source-owned records are read-only in the manual catalogue.', 409)


def records(programme, resource):
    if resource == 'groups':
        return CatalogGroup.objects.filter(cohort__programme=programme).select_related('cohort')
    if resource == 'deliveries':
        return CatalogDelivery.objects.filter(group__cohort__programme=programme).select_related('group__cohort', 'module_revision')
    if resource == 'modules':
        return programme.curriculum_modules.prefetch_related(
            'ksbs', 'weekly_topics',
            Prefetch('revisions', CatalogModuleRevision.objects.only('id', 'module_id', 'number')[:1], to_attr='latest_revisions'),
        )
    return getattr(programme, resource).all()


@catalog_access
@require_http_methods(['GET', 'POST'])
def programmes(request):
    if request.method == 'GET':
        active_cohorts = Q(archived_at__isnull=True, cohorts__archived_at__isnull=True)
        active_groups = active_cohorts & Q(cohorts__groups__archived_at__isnull=True)
        active_deliveries = active_groups & Q(cohorts__groups__deliveries__archived_at__isnull=True)
        records = CatalogProgramme.objects.annotate(
            cohort_count=Count('cohorts', distinct=True, filter=active_cohorts),
            group_count=Count('cohorts__groups', distinct=True, filter=active_groups),
            scheduled_module_count=Count('cohorts__groups__deliveries', distinct=True, filter=active_deliveries),
        )
        return JsonResponse({'programmes': ProgrammeListSerializer(records, many=True).data})
    with transaction.atomic():
        return save(ProgrammeSerializer, body(request))


@catalog_access
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def programme_detail(request, programme_id):
    if request.method == 'GET':
        programme = find(CatalogProgramme.objects.all(), programme_id)
        data = {'programme': ProgrammeSerializer(programme).data}
        for resource, serializer in RESOURCES.items():
            data[resource] = serializer(records(programme, resource), many=True).data
        return JsonResponse(data)

    with transaction.atomic():
        programme = find(CatalogProgramme.objects.select_for_update(), programme_id)
        require_manual(programme)
        check_revision(request, programme)
        if request.method == 'DELETE':
            return archive(programme, status=CatalogProgramme.Status.ARCHIVED)
        require_current(programme)
        return save(ProgrammeSerializer, body(request), programme)


@catalog_access
@require_http_methods(['GET', 'POST', 'PATCH', 'DELETE'])
def programme_records(request, programme_id, resource, record_id=None):
    serializer = RESOURCES.get(resource)
    if serializer is None:
        raise CatalogError('Unknown catalogue resource.', 404)
    allowed = ('GET', 'POST') if record_id is None else ('GET', 'PATCH', 'DELETE')
    if request.method not in allowed:
        return HttpResponse(status=405, headers={'Allow': ', '.join(allowed)})
    if request.method == 'GET':
        programme = find(CatalogProgramme.objects.all(), programme_id)
        queryset = records(programme, resource)
        if record_id is None:
            return JsonResponse({resource: serializer(queryset, many=True).data})
        return JsonResponse(serializer(find(queryset, record_id)).data)

    with transaction.atomic():
        programme = find(CatalogProgramme.objects.select_for_update(), programme_id)
        require_current(programme)
        require_manual(programme)
        if record_id is None:
            data = body(request)
            if resource == 'ksbs' and 'ksbs' in data:
                return import_ksbs(data, programme)
            parents = {'programme': programme} if resource in ('cohorts', 'modules', 'ksbs') else {}
            return save(serializer, data, programme=programme, parent_fields=parents)
        record = find(records(programme, resource), record_id)
        require_manual(record)
        check_revision(request, record)
        if resource == 'groups':
            require_current(record.cohort)
            require_manual(record.cohort)
        if resource == 'deliveries':
            require_current(record.group.cohort, record.group)
            require_manual(record.group.cohort, record.group)
        if request.method == 'DELETE':
            if resource == 'modules' and not record.archived_at and record.deliveries.filter(
                archived_at__isnull=True, group__archived_at__isnull=True, group__cohort__archived_at__isnull=True,
            ).exists():
                raise CatalogError('Archive the active deliveries before archiving this curriculum module.', 409)
            return archive(record)
        require_current(record)
        return save(serializer, body(request), record, programme=programme)


@catalog_access
@require_http_methods(['GET'])
def programme_pricing(request, programme_id):
    with transaction.atomic():
        programme = find(CatalogProgramme.objects.select_for_update(), programme_id)
        group = None
        pinned_revisions = []
        if 'group_id' in request.GET:
            require_current(programme)
            try:
                group_id = UUIDField().run_validation(request.GET['group_id'])
            except ValidationError as exc:
                raise ValidationError({'group_id': exc.detail}) from exc
            group = find(records(programme, 'groups').filter(
                archived_at__isnull=True, cohort__archived_at__isnull=True,
            ), group_id)
            pinned_revisions = [
                delivery.module_revision for delivery in group.deliveries.select_related('module_revision').filter(
                    archived_at__isnull=True,
                )
            ]
        quote = price_programme(programme, group, pinned_revisions)
    return JsonResponse({'pricing': quote, 'written_agreement_costs': written_agreement_costs(quote)})


@catalog_access
@require_http_methods(['GET'])
def staff_options(request):
    result = {'coaches': [], 'tutors': []}
    for staff in staff_members():
        grants = accesses_for_staff(staff)
        for role, key in [('coach', 'coaches'), ('tutor', 'tutors')]:
            if role in grants:
                result[key].append({'id': str(staff.uuid), 'name': staff.username.strip(), 'email': (staff.email or '').strip()})
    return JsonResponse(result)
