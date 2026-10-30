"""Shared helpers for the Manual audit API.

This app is a deliberately independent sibling of ``audit_api`` (the automatic
audit workspace). It owns the ``Manual_audit`` schema and never imports from
``audit_api`` so the two systems can evolve without affecting each other.
"""

import datetime
import os
import re
import time

from django.http import JsonResponse

try:
    from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
except ImportError:  # pragma: no cover - handled at runtime when Azure is not installed.
    BlobServiceClient = None
    BlobSasPermissions = None
    generate_blob_sas = None


# Both aliases point at the Neon database that hosts the "Last_audit" source
# schema and the "Manual_audit" schema this app owns.
CONN = "enrolment"
SCHEMA = "Manual_audit"


# Some LMS presentation components were historically mirrored as ``video``
# even though their catalogue title explicitly identifies them as a PPT.  The
# plan builder only has one non-media learning bucket (reading+quiz), so keep
# those presentation rows out of the video bucket at every API boundary.  The
# token boundaries avoid matching unrelated words that happen to contain
# "ppt".
_PRESENTATION_TITLE_RE = re.compile(
    r"(?<![a-z0-9])(?:ppt|power\s*point|powerpoint)(?![a-z0-9])",
    re.IGNORECASE,
)


def normalize_lms_category(activity_type, title=None):
    """Return the ledger category, correcting mislabeled LMS presentations."""
    value = str(activity_type or "activity").strip().lower()
    category = "reading+quiz" if value == "reading+quiz" else value
    if category == "video" and _PRESENTATION_TITLE_RE.search(str(title or "")):
        return "reading+quiz"
    return category


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def duration_min_sql(alias=""):
    """SQL for configured_duration_min with the audio fallback.  The ingestion
    pipeline stores audio durations only inside raw->audio, never on the
    column, so the column alone is NULL for every audio activity.  The regex
    guard keeps a malformed raw value from failing the whole query."""
    prefix = f"{alias}." if alias else ""
    json_path = f"{prefix}raw #>> '{{audio,configured_duration_min}}'"
    return (
        f"COALESCE({prefix}configured_duration_min, "
        f"(CASE WHEN {json_path} ~ '^[0-9]+(\\.[0-9]+)?$' THEN {json_path} END)::numeric)"
    )


# The read-only state is server-wide, so one cached answer serves every
# connection — without the cache each ensure-table call would add a round
# trip to Neon on every request. Read-only expires fast (recovery should be
# picked up quickly); writable lingers longer.
_READ_ONLY_CACHE = {"value": False, "expires": 0.0}


def db_is_read_only(cur):
    """Neon flips the endpoint read-only at times (plan limits, maintenance).
    DDL/writes abort then, so the lazy ensure-table helpers use this to keep
    the read paths alive — real writes still fail loudly on their own."""
    now = time.monotonic()
    if now < _READ_ONLY_CACHE["expires"]:
        return _READ_ONLY_CACHE["value"]
    try:
        cur.execute("select current_setting('transaction_read_only', true)")
        row = cur.fetchone()
    except Exception:  # non-Postgres backends (sqlite tests) have no GUCs
        return False
    value = bool(row) and str(row[0]).strip().lower() == "on"
    _READ_ONLY_CACHE["value"] = value
    _READ_ONLY_CACHE["expires"] = now + (5.0 if value else 15.0)
    return value


def _has_audit_permission(request, write=False):
    if os.environ.get("AUDIT_API_REQUIRE_AUTH", "").lower() not in {"1", "true", "yes"}:
        return True
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    permission = "audit.export" if write else "audit.view"
    return user.has_perm(permission)


def _azure_service_client():
    if BlobServiceClient is None:
        raise RuntimeError("The azure-storage-blob package is not installed.")
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not connection_string:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured.")
    return BlobServiceClient.from_connection_string(connection_string)


def _azure_connection_parts():
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    parts = {}
    for chunk in connection_string.split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key] = value
    return parts


def _blob_client_with_fallback(service, container, blob_name):
    client = service.get_blob_client(container=container, blob=blob_name)
    try:
        client.get_blob_properties()
        return client
    except Exception as exc:
        fallback_container = f"{container}s" if not container.endswith("s") else ""
        if not fallback_container or "ContainerNotFound" not in str(exc):
            raise
        fallback_client = service.get_blob_client(container=fallback_container, blob=blob_name)
        fallback_client.get_blob_properties()
        return fallback_client


def _signed_blob_url(client):
    parts = _azure_connection_parts()
    connection_sas = (parts.get("SharedAccessSignature") or "").lstrip("?")
    if connection_sas:
        separator = "&" if "?" in client.url else "?"
        return f"{client.url}{separator}{connection_sas}"

    account_name = parts.get("AccountName") or getattr(client, "account_name", "")
    account_key = parts.get("AccountKey")
    if not account_name or not account_key or generate_blob_sas is None or BlobSasPermissions is None:
        return client.url

    token = generate_blob_sas(
        account_name=account_name,
        container_name=client.container_name,
        blob_name=client.blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30),
    )
    return f"{client.url}?{token}"
