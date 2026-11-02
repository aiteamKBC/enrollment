"""Backend-only integration with the documented bksbLIVE2 API.

The provider exposes a legacy and a Reformed/V5 assessment session API.  This
module keeps the credentials and provider URLs on the server, resolves the
licensed English/Maths course subjects, and stores a small local projection so
the wizard remains usable when BKSB is temporarily unavailable.

No endpoint is guessed here: every path is present in BKSB's published API
reference.  In particular, V5 sessions/results are used only when the
licensed course advertises ``IsReforms``; the documented initial-session link
endpoint is used for both course generations until BKSB confirms a dedicated
V5 launch endpoint for the tenant.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx
from django.db import DatabaseError, IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt, csrf_protect, ensure_csrf_cookie

from learner_api.models import EnrolmentUser

from .auth import enrolment_login_required
from .models import BksbInitialAssessment, WizardPersonalDetails

logger = logging.getLogger(__name__)

SUBJECTS = ("english", "maths")
STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"


class BksbError(RuntimeError):
    """A safe, user-facing BKSB error (never contains credentials or tokens)."""


class BksbNotConfigured(BksbError):
    pass


class BksbIdentityMismatch(BksbError):
    pass


_PASSWORD_LOWER = string.ascii_lowercase
_PASSWORD_UPPER = string.ascii_uppercase
_PASSWORD_DIGITS = string.digits
_PASSWORD_SYMBOLS = "!#$%&*+-=?@^_"
_PASSWORD_ALPHABET = _PASSWORD_LOWER + _PASSWORD_UPPER + _PASSWORD_DIGITS + _PASSWORD_SYMBOLS


def _generate_provider_password(length: int = 32) -> str:
    """Generate a one-time provider password without persisting or logging it."""

    if length < 4:
        raise ValueError("Provider passwords must contain at least four characters.")
    password = [
        secrets.choice(_PASSWORD_LOWER),
        secrets.choice(_PASSWORD_UPPER),
        secrets.choice(_PASSWORD_DIGITS),
        secrets.choice(_PASSWORD_SYMBOLS),
    ]
    password.extend(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length - len(password)))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def _no_store(response: JsonResponse) -> JsonResponse:
    """Prevent browser/proxy caches from retaining provider login material."""
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return None


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, BksbError):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "BKSB did not respond in time. Please try again shortly."
    if isinstance(exc, httpx.RequestError):
        return "BKSB is temporarily unavailable. Please try again shortly."
    return "The BKSB assessment could not be synchronised. Please try again shortly."


def _safe_operation(method: str, path: str) -> str:
    """Return an endpoint breadcrumb with query/path identifiers removed."""
    clean_path = str(path or "").split("?", 1)[0]
    clean_path = re.sub(
        r"/(?:[0-9]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=/|$)",
        "/:id",
        clean_path,
        flags=re.IGNORECASE,
    )
    return f"{method.upper()} {clean_path}"


class BksbClient:
    """Small synchronous client for the official bksbLIVE2 endpoints."""

    def __init__(self, *, timeout: float = 15):
        self.base_url = (os.environ.get("BKSB_API_BASE_URL") or "https://live2api.bksblive2.co.uk/").strip().rstrip("/") + "/"
        self.access_key = os.environ.get("BKSB_ACCESS_KEY_ID", "").strip()
        self.secret_key = os.environ.get("BKSB_SECRET_ACCESS_KEY", "").strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return _truthy(os.environ.get("BKSB_ENABLED"))

    @property
    def configured(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def _require_configured(self) -> None:
        if not self.enabled:
            raise BksbNotConfigured("BKSB integration is not enabled.")
        if not self.configured:
            raise BksbNotConfigured("BKSB integration is not configured.")

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, payload: Any = None) -> Any:
        self._require_configured()
        operation = _safe_operation(method, path)
        # httpx's tuple auth emits the documented Basic access-key:secret-key
        # header.  The client is short-lived so a token/link can never leak into
        # a shared connection or a request log.
        try:
            with httpx.Client(
                base_url=self.base_url,
                auth=(self.access_key, self.secret_key),
                headers={"Accept": "application/json"},
                timeout=self.timeout,
                follow_redirects=False,
            ) as client:
                response = client.request(method, path, params=params, json=payload)
        except httpx.HTTPError:
            raise
        # Keep a diagnostic breadcrumb without query parameters, request
        # payloads, response bodies, access keys, or launch URLs.
        logger.info("BKSB API response %s -> HTTP %s", operation, response.status_code)
        if response.status_code < 200 or response.status_code >= 300:
            # Do not include response text: provider error bodies can echo
            # usernames, launch tokens, or other sensitive data.
            raise BksbError(f"BKSB returned HTTP {response.status_code}.")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise BksbError("BKSB returned an invalid response.") from exc

    def test_connection(self) -> bool:
        value = self._request("GET", "api/test/test")
        return bool(value is True or (isinstance(value, dict) and value.get("Data") in (True, "true", "True")))

    def licensed_courses(self) -> list[dict[str, Any]]:
        value = self._request("GET", "api/courses/licensedCourses")
        if not isinstance(value, list):
            raise BksbError("BKSB returned an invalid licensed-course list.")
        return [course for course in value if isinstance(course, dict)]

    def username_exists(self, username: str) -> bool:
        return bool(self._request("GET", "api/users/isUsernameExists", params={"username": username}))

    def user_id_for_username(self, username: str) -> int | None:
        try:
            value = self._request("GET", "api/users/user/getUserId", params={"username": username})
        except BksbError as exc:
            # A missing username is the documented lookup miss.  Treat only
            # that response as absent; permission/authentication errors must
            # still stop the flow rather than falling through to createUser.
            if str(exc) == "BKSB returned HTTP 404.":
                return None
            raise
        return _legacy_user_id(value)

    def user(self, user_id: int) -> dict[str, Any]:
        value = self._request("GET", f"api/users/user/{int(user_id)}")
        if not isinstance(value, dict):
            raise BksbError("BKSB returned an invalid user record.")
        return value

    def auto_login_link(self, user_id: int) -> dict[str, Any]:
        """Return the documented account Auto-login response.

        The response contains a short-lived login URL and a separate token.
        Callers must discard the token and must never log or persist either
        value.  This method performs no local or provider writes.
        """
        value = self._request("GET", f"api/users/user/{int(user_id)}/getAutoLoginLink")
        if not isinstance(value, dict):
            raise BksbError("BKSB returned an invalid Auto-login response.")
        return value

    def remove_request_change_password(self, user_id: int) -> dict[str, Any]:
        """Remove BKSB's next-login password-change request for one user."""

        value = self._request(
            "POST",
            f"api/users/user/{int(user_id)}/removeRequestChangePassword",
        )
        return value if isinstance(value, dict) else {}

    def create_user(self, *, username: str, first_name: str, last_name: str, email: str, dob: datetime | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "Username": username,
            "Firstname": first_name,
            "Lastname": last_name,
            # BKSB defaults this to the organisation-wide password when it is
            # omitted.  Use a fresh high-entropy value instead; it is sent only
            # over the TLS API call and is never returned, stored, or logged.
            "Password": _generate_provider_password(),
            "Email": email,
            "AutoEnrolCourses": False,
        }
        if dob:
            payload["DateOfBirth"] = {
                "Year": dob.year,
                "Month": dob.month,
                "Day": dob.day,
                "Hour": 0,
                "Minute": 0,
                "Second": 0,
                "Millisecond": 0,
            }
        value = self._request("POST", "api/users/createUser", payload=payload)
        if not isinstance(value, dict):
            raise BksbError("BKSB returned an invalid created-user record.")
        return value

    def get_user_courses(self, user_id: int) -> list[dict[str, Any]]:
        value = self._request("GET", f"api/users/user/{int(user_id)}/getCourses")
        if not isinstance(value, list):
            raise BksbError("BKSB returned an invalid user-course list.")
        return [course for course in value if isinstance(course, dict)]

    def enrol_course(self, user_id: int, course_subject: str) -> None:
        self._request("POST", f"api/users/user/{int(user_id)}/enrolCourse", params={"courseSubject": course_subject})

    def legacy_sessions(self, user_id: int, course_subject: str) -> list[dict[str, Any]]:
        value = self._request(
            "GET",
            "api/assessmentSessions/getInitialAssessmentSessionsForCourse",
            params={"courseSubject": course_subject, "userId": user_id},
        )
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def v5_sessions(self, user_id: int | str, course_subject: str) -> list[dict[str, Any]]:
        value = self._request(
            "GET",
            "api/assessmentSessions/getAssessmentSessionsForCourseV5",
            # V5 documents this parameter as a string because the response's
            # UserId is a GUID.  A legacy integer is still accepted for the
            # first lookup; once a V5 response is seen, callers pass its GUID.
            params={"courseSubject": course_subject, "userId": str(user_id)},
        )
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def course_level_v5(self, course_subject: str) -> dict[str, Any]:
        """Return BKSB's licensed V5 level boundaries for one course subject."""
        value = self._request(
            "GET",
            "api/courses/courseLevelV5",
            params={"courseSubject": course_subject},
        )
        if not isinstance(value, dict):
            raise BksbError("BKSB returned invalid course-level data.")
        return value

    def initial_session_link(self, user_id: int, course_subject: str) -> dict[str, Any]:
        value = self._request(
            "GET",
            "api/assessmentSessions/getInitialAssessmentSessionLinkForCourse",
            params={"courseSubject": course_subject, "userId": user_id},
        )
        return value if isinstance(value, dict) else {}

    def session_link(self, user_id: int, session_id: str) -> dict[str, Any]:
        value = self._request(
            "GET",
            "api/assessmentSessions/getAssessmentSessionLinkForSession",
            params={"sessionId": session_id, "userId": user_id},
        )
        return value if isinstance(value, dict) else {}

    def results(self, user_id: int, course_subject: str) -> list[dict[str, Any]]:
        value = self._request(
            "GET",
            f"api/results/initialAssessment/{int(user_id)}/bySubject",
            params={"page": 1, "recordsPerPage": 100, "courseSubject": course_subject},
        )
        if isinstance(value, dict):
            items = value.get("InitialAssessmentResults") or value.get("initialAssessmentResults") or []
            return [item for item in items if isinstance(item, dict)]
        return []

    def report_link(self, user_id: int, session_id: str) -> dict[str, Any]:
        value = self._request(
            "GET",
            "api/assessmentSessions/getEOAReportLinkForSession",
            params={"sessionId": session_id, "userId": user_id},
        )
        return value if isinstance(value, dict) else {}

    def report_data(self, user_id: int, session_id: str, *, reforms: bool) -> dict[str, Any]:
        path = (
            "api/assessmentSessions/GetEOAReportDataForAssessmentSessionV5"
            if reforms
            else "api/assessmentSessions/getEOAReportDataForInitialAssessmentSession"
        )
        value = self._request("GET", path, params={"sessionId": session_id, "userId": user_id})
        return value if isinstance(value, dict) else {}


def _learner(kind: str, learner_id: int) -> EnrolmentUser | None:
    if kind not in {"apprenticeship", "commercial"}:
        return None
    learner = EnrolmentUser.all_learners.filter(pk=learner_id).first()
    if learner is None:
        return None
    if kind == "commercial" and learner.learner_type not in {"commercial", "Commercial"}:
        return None
    if kind == "apprenticeship" and learner.learner_type in {"commercial", "Commercial"}:
        return None
    return learner


def _learner_identity(kind: str, learner_id: int, learner: EnrolmentUser) -> dict[str, Any]:
    username = str(learner.username or "").strip()
    first_name, _, last_name = username.partition(" ")
    try:
        details = WizardPersonalDetails.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    except DatabaseError:
        details = None
    if details:
        first_name = str(details.first_name or first_name).strip()
        last_name = str(details.last_name or last_name).strip()
    dob = _iso_datetime(learner.date_of_birth)
    email = str(learner.email or "").strip()
    return {
        "first_name": first_name or "Learner",
        "last_name": last_name or str(learner_id),
        "email": email,
        "dob": dob,
        "username": username,
        "aptem_id": str(getattr(learner, "aptem_id", "") or "").strip(),
    }


def _identity_matches(provider: dict[str, Any], identity: dict[str, Any]) -> bool:
    provider_email = _normalise(_first(provider, "Email", "email"))
    local_email = _normalise(identity.get("email"))
    if provider_email and local_email:
        return provider_email == local_email
    provider_name = _normalise(
        " ".join(filter(None, [str(_first(provider, "Firstname", "firstName") or ""), str(_first(provider, "Lastname", "lastName") or "")]))
    )
    local_name = _normalise(f"{identity.get('first_name', '')} {identity.get('last_name', '')}")
    return bool(provider_name and local_name and provider_name == local_name)


def _candidate_usernames(kind: str, learner_id: int, identity: dict[str, Any]) -> list[str]:
    candidates = []
    email = str(identity.get("email") or "").strip()
    if email:
        candidates.append(email)
    if identity.get("username"):
        candidates.append(str(identity["username"]).strip())
    if identity.get("aptem_id"):
        candidates.append(str(identity["aptem_id"]).strip())
    candidates.append(f"enrolment-{kind}-{learner_id}")
    out: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def _course_for_subject(courses: list[dict[str, Any]], subject: str) -> dict[str, Any]:
    tokens = ("math", "numeracy") if subject == "maths" else ("english", "literacy")
    matches = []
    for course in courses:
        if course.get("IsLicensed") is False or course.get("isLicensed") is False:
            continue
        text = " ".join(str(course.get(key) or "") for key in ("CourseSubject", "CourseName", "courseSubject", "courseName")).casefold()
        if any(token in text for token in tokens):
            matches.append(course)
    if not matches:
        raise BksbError(f"No licensed BKSB {subject} course was found.")
    matches.sort(key=lambda item: (0 if _normalise(_first(item, "CourseSubject", "courseSubject")) == subject else 1, str(_first(item, "CourseSubject", "courseSubject") or "")))
    return matches[0]


def _course_subject(course: dict[str, Any]) -> str:
    value = _first(course, "CourseSubject", "courseSubject")
    if not value:
        raise BksbError("BKSB returned a course without a subject.")
    return str(value)


def _direct_launch_enabled(course: dict[str, Any]) -> bool:
    """Allow direct links only when the tenant explicitly enables them.

    The dashboard Auto-login flow is the supported browser bridge.  Both
    reformed subjects currently use it by default; a direct session link is an
    opt-in escape hatch for a provider-confirmed course/endpoint pair.
    """
    subject = _normalise(_first(course, "CourseSubject", "courseSubject"))
    if subject == "eng_new":
        return _truthy(os.environ.get("BKSB_ENABLE_DIRECT_LAUNCH_ENG_NEW"))
    if subject == "maths_new":
        return _truthy(os.environ.get("BKSB_ENABLE_DIRECT_LAUNCH_MATHS_NEW"))
    return True


def _allowed_auto_login_hosts() -> set[str]:
    return {
        host.strip().lower().rstrip(".")
        for host in os.environ.get("BKSB_AUTO_LOGIN_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }


def _validated_auto_login_url(value: Any) -> str:
    """Accept only an HTTPS URL on an explicitly configured BKSB host."""
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or host not in _allowed_auto_login_hosts()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(ord(char) < 0x20 for char in url)
    ):
        raise BksbError("BKSB returned an unapproved Auto-login URL.")
    return url


def _legacy_user_id(value: Any) -> int | None:
    """Extract the integer BKSB id used by the documented legacy endpoints.

    Reformed/V5 payloads also contain ``UserId``, but that value is a GUID;
    ``LegacyUserId`` is the integer bridge required by the initial-launch,
    results and legacy session-link endpoints.  Never coerce a GUID by
    accident, and prefer the explicit legacy field when both are present.
    """
    if isinstance(value, dict):
        value = _first(value, "LegacyUserId", "legacyUserId", "UserId", "userId")
    if isinstance(value, bool) or value in (None, ""):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        return None
    user_id = int(text)
    return user_id if user_id > 0 else None


def _is_reforms(course: dict[str, Any]) -> bool:
    value = _first(course, "IsReforms", "isReforms")
    return bool(value is True or str(value).strip().lower() in {"1", "true", "yes"})


def _session_id(session: dict[str, Any]) -> str | None:
    value = _first(session, "SessionId", "sessionId")
    return str(value) if value is not None else None


def _session_complete(session: dict[str, Any]) -> bool:
    status = _first(session, "SessionStatus", "sessionStatus")
    if _first(session, "DateCompleted", "dateCompleted"):
        return True
    if isinstance(status, (int, float)):
        return int(status) == 1
    text = str(status or "").casefold()
    return "complete" in text


def _session_void(session: dict[str, Any]) -> bool:
    status = _first(session, "SessionStatus", "sessionStatus")
    if isinstance(status, (int, float)):
        return int(status) == 3  # documented Voided enum value
    return "void" in str(status or "").casefold()


def _is_initial_session(session: dict[str, Any]) -> bool:
    """Keep diagnostic/other sessions out of the initial-assessment card."""
    value = _first(session, "AssessmentType", "assessmentType", "SessionType", "sessionType")
    if value in (None, ""):
        # Legacy responses do not always include the enum; the endpoint itself
        # is initial-assessment-only, so an absent type is safe there.
        return True
    if isinstance(value, (int, float)):
        return int(value) == 1  # documented InitialAssessment enum value
    return "initial" in str(value).casefold()


def _v5_user_id(session: dict[str, Any], fallback: int) -> str | int:
    """Return the V5 GUID user id for V5 report-data, if supplied."""
    value = _first(session, "UserId", "userId")
    return str(value).strip() if value not in (None, "") else fallback


def _session_sort_key(session: dict[str, Any]) -> str:
    return str(_first(session, "DateStarted", "dateStarted", "DateCompleted", "dateCompleted") or "")


def _attempts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        session_id = _session_id(row)
        if not session_id:
            continue
        by_id[session_id] = {
            "sessionId": session_id,
            "userId": _first(row, "UserId", "userId"),
            "status": str(_first(row, "SessionStatus", "sessionStatus") or "").casefold() or "unknown",
            "startedAt": _first(row, "DateStarted", "dateStarted"),
            "completedAt": _first(row, "DateCompleted", "dateCompleted"),
        }
    return list(by_id.values())


def _stored_v5_user_id(row: BksbInitialAssessment) -> str | None:
    """Use a previously observed V5 GUID for subsequent session reads."""
    for attempt in reversed(row.attempts or []):
        if isinstance(attempt, dict):
            value = attempt.get("userId")
            if value not in (None, "") and not str(value).isdigit():
                return str(value)
    return None


def _result_value(result: dict[str, Any]) -> tuple[str | None, str | None, datetime | None]:
    result_text = _first(result, "Result", "result")
    # ``Result`` is the provider's result label.  It is not evidence of a
    # learner level; V5 report measurements have their own semantics and must
    # not be promoted to ``level`` without a documented mapping.
    level = _categorical_level(_first(result, "Level", "level", "WorkingLevel", "workingLevel"))
    date = _iso_datetime(_first(result, "ResultDate", "resultDate"))
    return (str(result_text) if result_text is not None else None, level, date)


_CATEGORICAL_LEVELS = {
    "pre-entry": "Pre-Entry",
    "pre entry": "Pre-Entry",
    "entry 1": "Entry 1",
    "entry 2": "Entry 2",
    "entry 3": "Entry 3",
    "level 1": "Level 1",
    "level 2": "Level 2",
    "level 3": "Level 3",
}


def _categorical_level(value: Any) -> str | None:
    """Accept only an explicit provider categorical level, never a score."""
    key = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    return _CATEGORICAL_LEVELS.get(key)


def _decimal_value(value: Any) -> Decimal | None:
    """Parse a finite provider measurement without binary floating-point loss."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _overall_level_from_report(report: dict[str, Any]) -> str | None:
    """Extract an explicit overall V5 level without classifying measurements."""
    data = _first(report, "AssessmentEOAData", "assessmentEOAData")
    if not isinstance(data, list):
        return None
    candidates = [item for item in data if isinstance(item, dict) and _as_bool(_first(item, "IsParent", "isParent"))]
    levels = {
        level
        for item in candidates
        if (level := _categorical_level(_first(item, "AbilityMeasurementName", "abilityMeasurementName")))
    }
    return levels.pop() if len(levels) == 1 else None


def _overall_measurement_from_report(report: dict[str, Any]) -> Decimal | None:
    """Return one unambiguous parent AbilityMeasurement, if present."""
    data = _first(report, "AssessmentEOAData", "assessmentEOAData")
    if not isinstance(data, list):
        return None
    measurements = {
        measurement
        for item in data
        if isinstance(item, dict)
        and _as_bool(_first(item, "IsParent", "isParent"))
        and (measurement := _decimal_value(_first(item, "AbilityMeasurement", "abilityMeasurement"))) is not None
    }
    return measurements.pop() if len(measurements) == 1 else None


def _map_measurement_to_course_level(
    course_levels: dict[str, Any],
    measurement: Decimal | str | int | float,
    *,
    expected_course_subject: str,
) -> tuple[str | None, str]:
    """Map only a strictly interior measurement to validated provider levels.

    BKSB publishes lower/upper boundaries but does not expose an inclusivity
    flag in this resource. Exact-boundary values therefore remain ambiguous
    until the tenant's provider policy documents which side owns the boundary.
    """
    if not isinstance(course_levels, dict):
        return None, "invalid_response"
    actual_subject = str(_first(course_levels, "CourseSubject", "courseSubject") or "").strip().casefold()
    if actual_subject != expected_course_subject.strip().casefold():
        return None, "wrong_course"
    if not _as_bool(_first(course_levels, "IsLicensed", "isLicensed")):
        return None, "course_not_licensed"
    raw_levels = _first(course_levels, "Levels", "levels")
    if not isinstance(raw_levels, list) or not raw_levels:
        return None, "missing_levels"

    parsed: list[tuple[Decimal, Decimal, str]] = []
    for item in raw_levels:
        if not isinstance(item, dict):
            return None, "invalid_level"
        lower = _decimal_value(_first(item, "LowerLevelBoundary", "lowerLevelBoundary"))
        upper = _decimal_value(_first(item, "UpperLevelBoundary", "upperLevelBoundary"))
        name = str(_first(item, "LevelName", "levelName") or "").strip()
        if lower is None or upper is None or not name or lower >= upper:
            return None, "invalid_level"
        parsed.append((lower, upper, name))

    parsed.sort(key=lambda item: item[0])
    for previous, current in zip(parsed, parsed[1:]):
        if current[0] > previous[1]:
            return None, "boundary_gap"
        if current[0] < previous[1]:
            return None, "boundary_overlap"

    value = _decimal_value(measurement)
    if value is None:
        return None, "invalid_measurement"
    if any(value == lower or value == upper for lower, upper, _ in parsed):
        return None, "boundary_inclusivity_unknown"
    matches = [name for lower, upper, name in parsed if lower < value < upper]
    if len(matches) != 1:
        return None, "outside_boundaries" if not matches else "ambiguous"
    return matches[0], "matched"


def _has_result_value(result: dict[str, Any]) -> bool:
    """Only a provider result value is completion evidence."""
    return bool(_first(result, "Result", "result"))


def _has_report_evidence(report: dict[str, Any]) -> bool:
    """Return whether a V5 EOA report contains actual assessment data."""
    data = _first(report, "AssessmentEOAData", "assessmentEOAData")
    return isinstance(data, list) and bool(data)


def _result_matches_session(result: dict[str, Any], session: dict[str, Any] | None) -> bool:
    """Prevent an older attempt's result completing a newer session."""
    if session is None:
        return True
    result_session_id = _session_id(result)
    session_id = _session_id(session)
    return bool(result_session_id and session_id and result_session_id == session_id)


def _set_error(row: BksbInitialAssessment, message: str) -> None:
    row.status = STATUS_ERROR
    row.last_error = message[:500]
    row.last_synced_at = datetime.now(timezone.utc)
    row.save(update_fields=["status", "last_error", "last_synced_at", "updated_at"])


def _sync_row(row: BksbInitialAssessment, client: BksbClient, course: dict[str, Any]) -> None:
    if not row.bksb_user_id:
        preserve_error = row.status == STATUS_ERROR and bool(row.last_error)
        row.course_subject = _course_subject(course)
        row.course_name = _first(course, "CourseName", "courseName")
        row.course_is_reforms = _is_reforms(course)
        if not preserve_error:
            row.status = STATUS_NOT_STARTED
            row.last_error = None
        row.last_synced_at = datetime.now(timezone.utc)
        row.save(update_fields=["course_subject", "course_name", "course_is_reforms", "status", "last_error", "last_synced_at", "updated_at"])
        return

    preserve_error = row.status == STATUS_ERROR and bool(row.last_error)
    course_subject = row.course_subject or _course_subject(course)
    v5_user_id = _stored_v5_user_id(row)
    sessions = client.v5_sessions(v5_user_id or row.bksb_user_id, course_subject) if row.course_is_reforms else client.legacy_sessions(row.bksb_user_id, course_subject)
    sessions = [item for item in sessions if _is_initial_session(item)]
    results = client.results(row.bksb_user_id, course_subject)
    row.attempts = _attempts(sessions)
    result_rows = [item for item in results if _has_result_value(item)]
    latest_result = max(result_rows, key=lambda item: str(_first(item, "ResultDate", "resultDate") or ""), default=None)
    latest_session = max((item for item in sessions if not _session_void(item)), key=_session_sort_key, default=None)
    report_evidence: dict[str, Any] | None = None
    report_level: str | None = None
    if latest_session and _session_complete(latest_session):
        # The report-data endpoint is the documented fallback for a completed
        # session whose paged result collection has not caught up yet, and is
        # also the source for a verified overall measurement when the result
        # row has no explicit categorical level. A failed report read does not
        # erase the completed session state.
        result_level = _categorical_level(_first(latest_result or {}, "Level", "level", "WorkingLevel", "workingLevel"))
        report_reader = getattr(client, "report_data", None)
        if callable(report_reader):
            try:
                if not latest_result or not result_level:
                    report = report_reader(
                        _v5_user_id(latest_session, row.bksb_user_id) if row.course_is_reforms else row.bksb_user_id,
                        _session_id(latest_session) or "",
                        reforms=bool(row.course_is_reforms),
                    )
                    if _has_report_evidence(report):
                        report_evidence = report
                        report_level = _overall_level_from_report(report)
                        if not report_level and row.course_is_reforms:
                            measurement = _overall_measurement_from_report(report)
                            if measurement is not None:
                                level_reader = getattr(client, "course_level_v5", None)
                                try:
                                    if not callable(level_reader):
                                        raise BksbError("BKSB course-level endpoint is unavailable.")
                                    level_name, _ = _map_measurement_to_course_level(
                                        level_reader(course_subject),
                                        measurement,
                                        expected_course_subject=course_subject,
                                    )
                                    report_level = _categorical_level(level_name)
                                except (BksbError, TypeError, ValueError):
                                    report_level = None
            except BksbError:
                report_evidence = None
    if latest_result and _result_matches_session(latest_result, latest_session):
        result_text, level, result_date = _result_value(latest_result)
        row.provider_session_id = str(_first(latest_result, "SessionId", "sessionId") or _session_id(latest_session) or "") or row.provider_session_id
        row.result = result_text
        row.level = level or report_level
        row.assessment_date = result_date or _iso_datetime(_first(latest_session or {}, "DateCompleted", "DateStarted"))
        row.status = STATUS_COMPLETED
    elif report_evidence:
        row.provider_session_id = _session_id(latest_session) or row.provider_session_id
        row.assessment_date = _iso_datetime(_first(latest_session or {}, "DateCompleted", "DateStarted"))
        if report_level:
            row.level = report_level
        row.status = STATUS_COMPLETED
    elif latest_session:
        # A provider session status alone is not a genuine result.  Keep the
        # projection resumable/reconcilable until result or report evidence is
        # available, even if the session itself is marked complete.
        row.provider_session_id = _session_id(latest_session)
        row.status = STATUS_IN_PROGRESS
    else:
        row.status = STATUS_NOT_STARTED
    if preserve_error and not latest_result and not latest_session:
        row.status = STATUS_ERROR
    else:
        row.last_error = None
    row.last_synced_at = datetime.now(timezone.utc)
    row.save(update_fields=["attempts", "provider_session_id", "result", "level", "assessment_date", "status", "last_error", "last_synced_at", "updated_at"])


def _get_or_create_row(kind: str, learner_id: int, subject: str) -> BksbInitialAssessment:
    try:
        row, _ = BksbInitialAssessment.objects.get_or_create(
            learner_kind=kind,
            learner_id=learner_id,
            subject=subject,
            defaults={"status": STATUS_NOT_STARTED, "attempts": []},
        )
        return row
    except IntegrityError:
        return BksbInitialAssessment.objects.get(learner_kind=kind, learner_id=learner_id, subject=subject)


def _ensure_provider_user(
    row: BksbInitialAssessment,
    client: BksbClient,
    kind: str,
    learner_id: int,
    learner: EnrolmentUser,
    *,
    return_created: bool = False,
) -> int | tuple[int, bool]:
    """Resolve or create the provider account and persist its mapping.

    ``return_created`` is used by the controlled first-login policy.  It is
    deliberately distinct from "the local row was previously unmapped": a
    deterministic username may resolve an account that already existed at
    BKSB, and that account must never have its password-change requirement
    altered by this workflow.
    """
    def result(user_id: int, created: bool) -> int | tuple[int, bool]:
        return (user_id, created) if return_created else user_id

    identity = _learner_identity(kind, learner_id, learner)
    if row.bksb_user_id:
        provider = client.user(row.bksb_user_id)
        if not _identity_matches(provider, identity):
            raise BksbIdentityMismatch("The stored BKSB user does not match this learner.")
        return result(int(row.bksb_user_id), False)

    for username in _candidate_usernames(kind, learner_id, identity):
        # getUserId is authoritative for this tenant.  isUsernameExists and
        # the general search endpoint have both shown eventual-consistency /
        # scope differences, so do not create a duplicate when they say false.
        provider_id = client.user_id_for_username(username)
        if not provider_id:
            continue
        provider = client.user(provider_id)
        if not _identity_matches(provider, identity):
            raise BksbIdentityMismatch("A BKSB username exists but could not be securely matched to this learner.")
        row.bksb_user_id = provider_id
        row.bksb_username = str(_first(provider, "Username", "username") or username)
        row.save(update_fields=["bksb_user_id", "bksb_username", "updated_at"])
        return result(provider_id, False)

    username = f"enrolment-{kind}-{learner_id}"
    provider = client.create_user(
        username=username,
        first_name=identity["first_name"],
        last_name=identity["last_name"],
        email=identity["email"],
        dob=identity["dob"],
    )
    provider_id = _legacy_user_id(provider)
    if provider_id is None:
        raise BksbError("BKSB did not return an integer legacy user id after creating the learner.")
    row.bksb_user_id = provider_id
    row.bksb_username = str(_first(provider, "Username", "username") or username)
    row.save(update_fields=["bksb_user_id", "bksb_username", "updated_at"])
    return result(provider_id, True)


def _remove_request_change_password_if_enabled(client: BksbClient, user_id: int, newly_created: bool) -> None:
    """Apply the opt-in suppression only after the local mapping is durable."""
    if not newly_created or not _truthy(os.environ.get("BKSB_REMOVE_REQUEST_CHANGE_PASSWORD_ON_CREATE")):
        return
    try:
        response = client.remove_request_change_password(user_id)
    except Exception as exc:  # noqa: BLE001 - provider boundary
        raise BksbError(
            "BKSB account was created, but first-login password-change suppression failed; manual reconciliation is required."
        ) from exc
    # A successful HTTP status alone is not enough: BKSB wraps operation
    # results in GenericResponseMessage and can report a provider-level
    # failure in the JSON body.
    status = _normalise(_first(response, "Status", "status")) if isinstance(response, dict) else ""
    if status not in {"success", "succeeded", "ok"}:
        raise BksbError(
            "BKSB account was created, but first-login password-change suppression was not confirmed; manual reconciliation is required."
        )


def _reconcile_provider_user_mapping(
    row: BksbInitialAssessment,
    client: BksbClient,
    kind: str,
    learner_id: int,
    learner: EnrolmentUser,
) -> None:
    """Preserve a provider user when a later launch step fails.

    User creation and course/session calls are external side effects.  If a
    later call fails, the surrounding local transaction may roll back the
    mapping even though BKSB kept the user.  Re-read only documented user
    lookups and save a mapping only after an exact identity match; never create
    or enrol from this recovery path.
    """
    if row.bksb_user_id:
        return
    identity = _learner_identity(kind, learner_id, learner)
    for username in _candidate_usernames(kind, learner_id, identity):
        try:
            provider_id = client.user_id_for_username(username)
        except BksbError:
            continue
        if not provider_id:
            continue
        try:
            provider = client.user(provider_id)
        except BksbError:
            continue
        if not _identity_matches(provider, identity):
            continue
        row.bksb_user_id = provider_id
        row.bksb_username = str(_first(provider, "Username", "username") or username)
        row.save(update_fields=["bksb_user_id", "bksb_username", "updated_at"])
        return


def _ensure_enrolled(row: BksbInitialAssessment, client: BksbClient, user_id: int, course: dict[str, Any]) -> str:
    subject = _course_subject(course)
    courses = client.get_user_courses(user_id)
    enrolled = next((item for item in courses if _normalise(_first(item, "CourseSubject", "courseSubject")) == _normalise(subject)), None)
    if not enrolled or not _as_bool(_first(enrolled, "IsEnrolled", "isEnrolled")):
        client.enrol_course(user_id, subject)
        # The enrolment call is an external write.  Re-read the provider state
        # so a successful HTTP response is not mistaken for a committed
        # enrolment when BKSB is eventually consistent or partially failed.
        courses = client.get_user_courses(user_id)
        enrolled = next((item for item in courses if _normalise(_first(item, "CourseSubject", "courseSubject")) == _normalise(subject)), None)
    if not enrolled or not _as_bool(_first(enrolled, "IsEnrolled", "isEnrolled")):
        raise BksbError("BKSB course enrolment could not be verified.")
    return subject


def _provision_account_for_auto_login(
    row: BksbInitialAssessment,
    client: BksbClient,
    kind: str,
    learner_id: int,
    learner: EnrolmentUser,
) -> tuple[BksbInitialAssessment, int]:
    """Commit a verified provider mapping before any later provisioning step."""

    try:
        with transaction.atomic(using="enrolment"):
            locked = BksbInitialAssessment.objects.select_for_update().get(pk=row.pk)
            user_id, newly_created = _ensure_provider_user(
                locked, client, kind, learner_id, learner, return_created=True
            )
        # The create response is not sufficient proof that the returned ID belongs
        # to this learner.  Verify the provider record after the mapping commit.
        provider = client.user(user_id)
        if not _identity_matches(provider, _learner_identity(kind, learner_id, learner)):
            raise BksbIdentityMismatch("The BKSB account could not be verified for this learner.")
        # This is intentionally outside the mapping transaction.  A provider
        # policy call can fail after createUser succeeds; the durable mapping
        # is then available for reconciliation instead of being rolled back.
        _remove_request_change_password_if_enabled(client, user_id, newly_created)
    except Exception:
        # createUser may have succeeded even when a subsequent local save or
        # opt-in suppression call failed.  Reconcile by exact identity lookup;
        # never create another account from this recovery path.
        try:
            # Reconcile on the same subject row that was being provisioned.
            # Maths (MATHS_NEW) must share the provider identity with English,
            # but a recovery mapping still belongs to the subject whose flow
            # failed; hard-coding the English row can leave the Maths row
            # unmapped and trigger a duplicate create on retry.
            recovery_subject = str(getattr(row, "subject", "") or "").strip().casefold()
            if recovery_subject not in SUBJECTS:
                recovery_subject = "english"
            recovery = _get_or_create_row(kind, learner_id, recovery_subject)
            _reconcile_provider_user_mapping(recovery, client, kind, learner_id, learner)
        except DatabaseError:
            pass
        raise

    return locked, user_id


def _status_json(row: BksbInitialAssessment) -> dict[str, Any]:
    # Provider access is intentionally independent from assessment progress.
    # A mapped/enrolled account can open the BKSB dashboard, but that does not
    # mean an assessment was started or completed.
    access_ready = bool(row.bksb_user_id and row.course_subject)
    return {
        "subject": row.subject,
        "status": row.status,
        "accessStatus": "ready" if access_ready else "not_ready",
        "bksbAccountReady": access_ready,
        "courseSubject": row.course_subject,
        "courseName": row.course_name,
        "result": row.result,
        "level": row.level,
        "assessmentDate": row.assessment_date.isoformat() if row.assessment_date else None,
        "lastSyncedAt": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "error": row.last_error,
        "attempts": row.attempts or [],
    }


def _learner_error(kind: str, learner_id: int) -> JsonResponse | None:
    if _learner(kind, learner_id) is None:
        return JsonResponse({"error": "Learner not found."}, status=404)
    return None


@enrolment_login_required
def bksb_connection_test(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    client = BksbClient()
    if not client.enabled:
        return JsonResponse({"enabled": False, "configured": client.configured, "connected": False})
    try:
        connected = client.test_connection()
        courses = client.licensed_courses()
        return JsonResponse({"enabled": True, "configured": True, "connected": connected, "courseCount": len(courses)})
    except Exception as exc:  # noqa: BLE001 - external integration boundary
        logger.warning("BKSB connection test failed: %s", _safe_error(exc))
        return JsonResponse({"enabled": True, "configured": client.configured, "connected": False, "error": _safe_error(exc)}, status=502)


@enrolment_login_required
def bksb_courses(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    client = BksbClient()
    try:
        courses = client.licensed_courses()
    except Exception as exc:  # noqa: BLE001 - external integration boundary
        return JsonResponse({"error": _safe_error(exc)}, status=502)
    return JsonResponse({"courses": courses})


@enrolment_login_required
@ensure_csrf_cookie
def initial_assessments(request, kind, learner_id):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    missing = _learner_error(kind, learner_id)
    if missing:
        return missing
    client = BksbClient()
    if not client.enabled:
        return JsonResponse({"enabled": False, "assessments": [{"subject": subject, "status": STATUS_ERROR, "error": "BKSB is unavailable.", "directLaunchEnabled": False} for subject in SUBJECTS]})
    try:
        courses = client.licensed_courses()
        output = []
        for subject in SUBJECTS:
            row = _get_or_create_row(kind, learner_id, subject)
            direct_launch_enabled = False
            try:
                course = _course_for_subject(courses, subject)
                _sync_row(row, client, course)
                direct_launch_enabled = _direct_launch_enabled(course)
            except Exception as exc:  # noqa: BLE001 - one subject must not hide the other
                logger.warning("BKSB status sync failed subject=%s: %s", subject, _safe_error(exc))
                _set_error(row, _safe_error(exc))
            item = _status_json(row)
            item["directLaunchEnabled"] = direct_launch_enabled
            output.append(item)
        return JsonResponse({"enabled": True, "assessments": output})
    except Exception as exc:  # noqa: BLE001 - external integration boundary
        logger.warning("BKSB status request failed: %s", _safe_error(exc))
        return JsonResponse({"enabled": True, "assessments": [{"subject": subject, "status": STATUS_ERROR, "error": _safe_error(exc), "directLaunchEnabled": False} for subject in SUBJECTS]}, status=502)


@enrolment_login_required
@csrf_exempt
def launch_initial_assessment(request, kind, learner_id, subject):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    if subject not in SUBJECTS:
        return JsonResponse({"error": "Unknown assessment subject."}, status=400)
    missing = _learner_error(kind, learner_id)
    if missing:
        return missing
    client = BksbClient()
    try:
        courses = client.licensed_courses()
        course = _course_for_subject(courses, subject)
        if not _direct_launch_enabled(course):
            return JsonResponse({"error": "Direct BKSB launch is temporarily unavailable for this course. Use Open BKSB Account."}, status=409)
        # Commit the provider-user mapping before any later provider operation.
        # A failed course or session call must not roll a successful createUser
        # mapping back; the exception path can then reconcile it by identity.
        with transaction.atomic(using="enrolment"):
            row = _get_or_create_row(kind, learner_id, subject)
            row = BksbInitialAssessment.objects.select_for_update().get(pk=row.pk)
            learner = _learner(kind, learner_id)
            assert learner is not None
            user_id, newly_created = _ensure_provider_user(
                row, client, kind, learner_id, learner, return_created=True
            )

        provider = client.user(user_id)
        if not _identity_matches(provider, _learner_identity(kind, learner_id, learner)):
            raise BksbIdentityMismatch("The BKSB account could not be verified for this learner.")
        _remove_request_change_password_if_enabled(client, user_id, newly_created)
        course_subject = _ensure_enrolled(row, client, user_id, course)
        reforms = _is_reforms(course)
        with transaction.atomic(using="enrolment"):
            row = BksbInitialAssessment.objects.select_for_update().get(pk=row.pk)
            row.course_subject = course_subject
            row.course_name = _first(course, "CourseName", "courseName")
            row.course_is_reforms = reforms
            row.save(update_fields=["course_subject", "course_name", "course_is_reforms", "updated_at"])

        # Everything below runs after the mapping/course transaction has
        # committed.  A provider failure here therefore cannot erase a user
        # mapping or enrolment reconciliation that already succeeded.
        sessions = client.v5_sessions(user_id, course_subject) if reforms else client.legacy_sessions(user_id, course_subject)
        sessions = [item for item in sessions if _is_initial_session(item)]
        current = max((item for item in sessions if not _session_void(item) and not _session_complete(item)), key=_session_sort_key, default=None)
        completed_session = max((item for item in sessions if not _session_void(item) and _session_complete(item)), key=_session_sort_key, default=None)
        if current:
            if reforms:
                # The published resume endpoint accepts integer session ids,
                # while V5 sessions expose GUIDs.  There is no documented
                # V5 GUID resume endpoint, so do not guess that opening the
                # initial link resumes this attempt or create a new one.
                raise BksbError("BKSB V5 resume requires a provider-confirmed endpoint.")
            session_id = _session_id(current)
            link = client.session_link(user_id, session_id) if session_id else {}
            kind_of_link = "assessment"
            row.provider_session_id = session_id
            row.status = STATUS_IN_PROGRESS
        elif row.status == STATUS_IN_PROGRESS:
            # A launch response can arrive before the provider exposes the
            # new session in the listing.  Never call the initial-link GET
            # again in that window: BKSB may create another attempt as a side
            # effect.  Legacy sessions can be resumed with their integer id;
            # V5 requires a provider-confirmed GUID resume endpoint.
            if reforms:
                raise BksbError("BKSB V5 resume requires a provider-confirmed endpoint.")
            if not row.provider_session_id:
                raise BksbError("BKSB has not returned a resumable session id yet.")
            link = client.session_link(user_id, row.provider_session_id)
            kind_of_link = "assessment"
        elif row.status == STATUS_ERROR and row.last_error:
            # A previous launch may have reached BKSB before the response was
            # lost.  Do not issue another initial-link request blindly; an
            # operator must reconcile the provider session first.
            raise BksbError("A previous BKSB launch needs reconciliation before it can be retried.")
        elif completed_session or (row.status == STATUS_COMPLETED and row.provider_session_id):
            if completed_session:
                row.provider_session_id = _session_id(completed_session) or row.provider_session_id
            row.status = STATUS_COMPLETED
            if reforms:
                raise BksbError("BKSB V5 report links require a provider-confirmed endpoint.")
            link = client.report_link(user_id, row.provider_session_id)
            kind_of_link = "report"
        else:
            # Commit a launch guard before the documented initial-link GET.
            # This prevents two browser clicks from both creating an attempt
            # while BKSB's session list is still eventually consistent.
            with transaction.atomic(using="enrolment"):
                guarded = BksbInitialAssessment.objects.select_for_update().get(pk=row.pk)
                if guarded.status == STATUS_IN_PROGRESS or (guarded.status == STATUS_ERROR and guarded.last_error):
                    raise BksbError("A previous BKSB launch needs reconciliation before it can be retried.")
                guarded.status = STATUS_IN_PROGRESS
                guarded.last_error = None
                guarded.save(update_fields=["status", "last_error", "updated_at"])
                row.status = guarded.status
            link = client.initial_session_link(user_id, course_subject)
            kind_of_link = "assessment"
            row.provider_session_id = str(_first((link.get("Session") or {}), "SessionId", "sessionId") or row.provider_session_id or "") or None
            row.status = STATUS_IN_PROGRESS
        url = _first(link, "Link", "link")
        if not url:
            raise BksbError("BKSB did not return a launch link.")
        next_provider_session_id = row.provider_session_id
        next_status = row.status
        with transaction.atomic(using="enrolment"):
            row = BksbInitialAssessment.objects.select_for_update().get(pk=row.pk)
            row.provider_session_id = next_provider_session_id
            row.status = next_status
            row.last_error = None
            row.last_synced_at = datetime.now(timezone.utc)
            row.save(update_fields=["provider_session_id", "status", "last_error", "last_synced_at", "updated_at"])
        # The short-lived URL is returned to the authorised caller only.  It is
        # intentionally never logged or persisted in the database.
        return JsonResponse({"subject": subject, "kind": kind_of_link, "url": url, "expiresAt": _first(link, "Expiry", "expiry")})
    except Exception as exc:  # noqa: BLE001 - external integration boundary
        message = _safe_error(exc)
        logger.warning("BKSB launch failed subject=%s: %s", subject, message)
        try:
            row = _get_or_create_row(kind, learner_id, subject)
            learner = _learner(kind, learner_id)
            if learner is not None:
                _reconcile_provider_user_mapping(row, client, kind, learner_id, learner)
            _set_error(row, message)
        except DatabaseError:
            pass
        return JsonResponse({"error": message}, status=502)


@enrolment_login_required
@csrf_protect
def auto_login_account(request, kind, learner_id):
    """Provision (if needed) and open the learner's BKSB account."""
    if request.method != "POST":
        return _no_store(JsonResponse({"error": "Method not allowed."}, status=405))
    missing = _learner_error(kind, learner_id)
    if missing:
        return _no_store(missing)
    try:
        requested_subject = str(request.GET.get("subject") or "english").strip().casefold()
        if requested_subject not in SUBJECTS:
            return _no_store(JsonResponse({"error": "Unknown assessment subject."}, status=400))
        learner = _learner(kind, learner_id)
        if learner is None:
            return _no_store(JsonResponse({"error": "Learner not found."}, status=404))
        client = BksbClient()
        courses = client.licensed_courses()
        course = _course_for_subject(courses, requested_subject)
        row = _get_or_create_row(kind, learner_id, requested_subject)
        row, user_id = _provision_account_for_auto_login(row, client, kind, learner_id, learner)
        course_subject = _ensure_enrolled(row, client, user_id, course)
        with transaction.atomic(using="enrolment"):
            saved = BksbInitialAssessment.objects.select_for_update().get(pk=row.pk)
            if int(saved.bksb_user_id or 0) != int(user_id):
                raise BksbError("The BKSB account mapping changed during provisioning.")
            saved.course_subject = course_subject
            saved.course_name = _first(course, "CourseName", "courseName")
            saved.course_is_reforms = _is_reforms(course)
            saved.last_error = None
            saved.last_synced_at = datetime.now(timezone.utc)
            saved.save(update_fields=["course_subject", "course_name", "course_is_reforms", "last_error", "last_synced_at", "updated_at"])
        # Auto-login remains the only browser-facing credential bridge.  The
        # URL is short-lived, validated against the explicit host allowlist,
        # and never persisted or logged.
        link = client.auto_login_link(user_id)
        url = _validated_auto_login_url(_first(link, "Link", "link"))
        return _no_store(JsonResponse({"url": url, "expiresAt": _first(link, "Expiry", "expiry")}))
    except Exception as exc:  # noqa: BLE001 - external integration boundary
        logger.warning("BKSB Auto-login failed: %s", _safe_error(exc))
        status = 502 if not isinstance(exc, BksbIdentityMismatch) else 409
        return _no_store(JsonResponse({"error": _safe_error(exc)}, status=status))
