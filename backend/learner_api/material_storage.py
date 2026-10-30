"""Private material files use their recorded account, independently of evidence."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import re
import urllib.error
import urllib.request

from azure.storage.blob import generate_blob_sas, BlobSasPermissions


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, target):
        return None


def curriculum_archive_url(row, settings):
    """Reuse the existing same-origin delivery route for verified legacy blobs."""
    account = getattr(settings, 'AZURE_STORAGE_ACCOUNT', '')
    container = getattr(settings, 'AZURE_CURRICULUM_CONTAINER', '')
    name = row.get('material_blob_name') or ''
    if (not account or not container
            or row.get('material_blob_account') != account
            or row.get('material_blob_container') != container
            or not re.fullmatch(r'_legacy_files/[0-9]+/[^/\\\x00-\x1f]+', name)
            or name.rsplit('/', 1)[-1] in {'.', '..'}):
        return ''
    return '/curriculum_api/curriculum/uploads/' + quote(name, safe='/')


def read_url(row, settings):
    account = row.get('material_blob_account')
    if not account:
        # Existing backups predate the dedicated material account.
        account, key = settings.AZURE_STORAGE_ACCOUNT, settings.AZURE_STORAGE_KEY
    elif account == settings.AZURE_MATERIALS_STORAGE_ACCOUNT:
        key = settings.AZURE_MATERIALS_STORAGE_KEY
    elif account == settings.AZURE_STORAGE_ACCOUNT:
        key = settings.AZURE_STORAGE_KEY
    else:
        raise ValueError('Unknown material storage account')
    if not account or not key:
        raise ValueError('Material storage is not configured')
    container, blob = row['material_blob_container'], row['material_blob_name']
    minutes = min(60, max(1, settings.AZURE_MATERIALS_SAS_TTL_MINUTES))
    token = generate_blob_sas(account_name=account, account_key=key,
        container_name=container, blob_name=blob, permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=minutes))
    return f'https://{account}.blob.core.windows.net/{quote(container, safe="")}/{quote(blob, safe="/")}?{token}'


def pdf_response(row, settings, request):
    """Serve an already-authorized PDF on the LMS origin, without browser CORS."""
    from django.http import FileResponse, HttpResponse

    headers = {'Accept': 'application/pdf'}
    if request.headers.get('Range'):
        headers['Range'] = request.headers['Range']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        upstream = opener.open(urllib.request.Request(read_url(row, settings), headers=headers), timeout=30)
    except urllib.error.HTTPError as error:
        if error.code == 416:
            response = HttpResponse(status=416)
            if error.headers.get('Content-Range'):
                response['Content-Range'] = error.headers['Content-Range']
            error.close()
            return response
        error.close()
        raise ValueError('The stored PDF is temporarily unavailable.') from None
    except OSError:
        raise ValueError('The stored PDF is temporarily unavailable.') from None
    if upstream.status not in (200, 206) or upstream.headers.get_content_type() != 'application/pdf':
        upstream.close()
        raise ValueError('The stored file is not an available PDF.')
    response = FileResponse(upstream, status=upstream.status, content_type='application/pdf',
        as_attachment=False, filename='material.pdf')
    for header in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
        if upstream.headers.get(header):
            response[header] = upstream.headers[header]
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    return response


def file_response(row, settings, request):
    """Serve any other stored material on the LMS origin.

    Redirecting to the SAS URL left the browser fetching a Word or Excel file
    from Azure, which sends no CORS headers for this site, so the learner's
    inline preview failed with "Failed to fetch". Same idea as ``pdf_response``,
    without its PDF-only check.
    """
    from django.http import FileResponse, HttpResponse

    headers = {}
    if request.headers.get('Range'):
        headers['Range'] = request.headers['Range']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        upstream = opener.open(urllib.request.Request(read_url(row, settings), headers=headers), timeout=30)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if status == 416:
            return HttpResponse(status=416)
        raise ValueError('The stored file is temporarily unavailable.') from None
    except OSError:
        raise ValueError('The stored file is temporarily unavailable.') from None
    if upstream.status not in (200, 206):
        upstream.close()
        raise ValueError('The stored file is temporarily unavailable.')
    filename = (row.get('material_blob_name') or 'material').rsplit('/', 1)[-1]
    content_type = (row.get('material_blob_content_type')
                    or upstream.headers.get_content_type() or 'application/octet-stream')
    response = FileResponse(upstream, status=upstream.status, content_type=content_type,
        as_attachment=False, filename=filename)
    for header in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
        if upstream.headers.get(header):
            response[header] = upstream.headers[header]
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    return response


#: Stored material the browser cannot draw; ``?preview=1`` hands these to Office.
OFFICE_PREVIEW_SUFFIXES = ('.doc', '.docx', '.docm', '.rtf', '.odt', '.xls', '.xlsx', '.ods',
                           '.ppt', '.pptx', '.pptm', '.pps', '.ppsx', '.odp')


def office_preview_response(row, settings):
    """A same-origin page framing Office Online on a short-lived SAS URL.

    Office downloads the file from Microsoft's servers, which can read a SAS
    URL but not anything behind the learner's session.
    """
    from html import escape
    from django.http import HttpResponse

    filename = (row.get('material_blob_name') or 'material').rsplit('/', 1)[-1]
    viewer = 'https://view.officeapps.live.com/op/embed.aspx?src=' + quote(read_url(row, settings), safe='')
    response = HttpResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(filename)}</title>'
        '<style>html, body, iframe { width: 100%; height: 100%; margin: 0; border: 0; }</style></head>'
        f'<body><iframe src="{escape(viewer, quote=True)}" title="{escape(filename, quote=True)}"></iframe></body></html>',
        content_type='text/html; charset=utf-8',
    )
    response['Content-Security-Policy'] = (
        "default-src 'none'; frame-src https://view.officeapps.live.com; "
        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    )
    response['X-Frame-Options'] = 'SAMEORIGIN'
    return response
