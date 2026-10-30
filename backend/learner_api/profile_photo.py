"""Learner avatars: validated, re-encoded photos stored privately by enrolment ID.

No database field is needed: a learner's canonical blob has a deterministic key.
Original uploads and image metadata are never stored or served.
"""
from io import BytesIO
import logging
import warnings

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from PIL import Image, ImageOps, UnidentifiedImageError

from login.permissions import learner_self_or_staff
from . import evidence_storage
from .learner_detail import SOURCE_MODELS

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PHOTO_BYTES = 1024 * 1024
MAX_PIXELS = 20_000_000
REQUEST_OPTIONS = {'connection_timeout': 5, 'read_timeout': 15}


def normalize_photo(upload, max_upload_bytes=MAX_UPLOAD_BYTES):
    limit_mb = max_upload_bytes // (1024 * 1024)
    if upload.size > max_upload_bytes:
        raise ValueError(f'Choose a photo no larger than {limit_mb} MB.')
    raw = upload.read(max_upload_bytes + 1)
    if not raw or len(raw) > max_upload_bytes:
        raise ValueError(f'Choose a photo no larger than {limit_mb} MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as original:
                if original.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('Choose a JPG, PNG or WebP photo.')
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError('Choose a photo with no more than 20 megapixels.')
                original.load()
                oriented = ImageOps.exif_transpose(original)
                fitted = ImageOps.fit(oriented, (512, 512), method=Image.Resampling.LANCZOS).convert('RGBA')
                # Flatten transparency onto a fresh canvas; no EXIF/GPS or
                # user-provided metadata reaches the generated JPEG.
                canvas = Image.new('RGB', fitted.size, 'white')
                canvas.paste(fitted, mask=fitted.getchannel('A'))
                output = BytesIO()
                canvas.save(output, format='JPEG', quality=88, optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('This photo could not be read. Choose a valid JPG, PNG or WebP image.') from error


def photo_bytes(learner_id, content=None):
    """Read, or atomically replace, the generated image using existing Azure credentials."""
    if not evidence_storage.azure_configured():
        raise RuntimeError('Photo storage is not configured.')
    container = getattr(settings, 'AZURE_LEARNER_PHOTOS_CONTAINER', 'learner-photos')
    # Both learner kinds use the same Created_users primary-key sequence.
    blob_name = f'enrolment/{int(learner_id)}/profile.jpg'
    with evidence_storage._service_client(retry_total=0) as service:
        blob = service.get_blob_client(container=container, blob=blob_name)
        if content is not None:
            try:
                # Private by default. Creation happens only on an explicit upload.
                service.get_container_client(container).create_container(**REQUEST_OPTIONS)
            except ResourceExistsError:
                pass
            blob.upload_blob(content, overwrite=True, max_concurrency=1,
                             content_settings=ContentSettings(content_type='image/jpeg', cache_control='private, no-store'),
                             **REQUEST_OPTIONS)
            return content
        try:
            content = blob.download_blob(offset=0, length=MAX_PHOTO_BYTES + 1, **REQUEST_OPTIONS).readall()
        except ResourceNotFoundError:
            return None
        if len(content) > MAX_PHOTO_BYTES:
            raise RuntimeError('Stored photo is too large.')
        return content


@require_http_methods(['GET', 'POST'])
@learner_self_or_staff(kwarg='pk')
def learner_profile_photo(request, kind, pk):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    try:
        if not model.all_learners.filter(pk=pk).exists():
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        if request.method == 'POST':
            upload = request.FILES.get('photo')
            if upload is None:
                return JsonResponse({'error': 'Choose a photo to upload.'}, status=400)
            content = photo_bytes(pk, normalize_photo(upload))
        else:
            content = photo_bytes(pk)
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
    except (AzureError, DatabaseError, RuntimeError):
        # Do not expose signed storage URLs or account configuration to clients.
        logger.warning('Profile photo storage unavailable for learner %s', pk)
        return JsonResponse({'error': 'Your photo could not be saved or loaded. Please try again.'}, status=503)
    response = HttpResponse(content, content_type='image/jpeg') if content is not None else HttpResponse(status=204)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
