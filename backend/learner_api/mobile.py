"""Learner-only mobile facade over the existing LMS services.

Copy into learner_api/mobile.py; mount mobile_urls below learner_api/mobile/.
No bearer auth, new identity store, alternate points ledger or direct progress SQL.
Disabled until KBC_MOBILE_API_ENABLED=True is explicitly set on the LMS host.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import io
import json
import math
import os
import re

from django.conf import settings
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from login.api_gate import refusal_for
from login.android_download_access import staff_download_allowed
from login.permissions import auth_gate_enabled
from login.sessions import authenticate_request, session_unreadable
from .models import EnrolmentUser, LearnerProfile
from .learner_detail import build_learner_detail
from .learning_plan import effective_training_plan
from .mobile_progress import measured_time, project_progress
from .presentation_json import presentation_object
from .android_download import installer_response
from curriculum_api.week_visibility import visible_child_sql, visible_week_sql

BASE = "/learner_api/mobile/"
ANDROID_APK_PATH = "/var/lib/kbc-mobile-release/20261013/KBC-Learn-LMS-internal-candidate.apk"
PILOT_MODULE_ID = "MOD-KBC-PMP-M01-SMR"
PILOT_MODULE_TITLE = "Stakeholder Management and Reporting"
MAX_BODY_BYTES = 64 * 1024
DISABLED = {"0", "false", "no", "off"}
CHOICE_QUESTION_TYPES = {"single_choice", "true_false", "multiple_choice"}
MEDIA_COMPONENT_TYPES = {"audio", "podcast", "reading", "video"}
ACTIVITY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}")
CAPABILITIES = {
    "cookieLogin": True, "assignedCatalogue": True, "canonicalProgress": True,
    "signedTiming": True, "quizSubmission": True, "pointsRead": True,
    "calendarRead": True, "customPointsPolicy": False, "offlineHoursSync": False,
    "timeObservations": False, "aiMarkingReports": False, "sharedBookingsSync": False,
    "microsoftNativeHandoff": False, "crossDeviceTimerDeduplication": False,
}
COMPONENT_FIELDS = {
    "componentId", "moduleId", "weekId", "module", "week", "component", "type",
    "description", "audioUrl", "videoUrl", "alternativeYouTubeUrl", "durationMinutes", "expectedOtjh",
    "reflectionPrompt", "reflectionRequired", "reflectionQuestion",
    "tutorValidationRequired", "ksbMappings", "ksbMappingCount", "ksbWeightTotal",
    "isQuiz", "quizMeta",
}
PROGRESS_FIELDS = {
    "quizAttempts", "videoProgress", "componentProgress", "componentMarkingStatus",
    "ksbs", "progressKsbCodes", "totalExpectedOtjh", "totalCompletedOtjh",
    "plannedHours", "completedHours", "targetHours", "progressHours",
    "progressVariance", "otjhStatus", "currentModule", "currentWeek",
    "componentsTargetToDate",
}
COMPLETION_FIELDS = {
    "trackingToken", "timeTakenSeconds", "feedback", "skipReflection",
    "declaredCompletedAt", "answers",
}


class MobileError(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        self.message, self.status, self.code = message, status, code


def _error(message, status, code):
    response = JsonResponse({"error": message, "code": code}, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def _body(request):
    if len(request.body) > MAX_BODY_BYTES:
        raise MobileError("The request is too large.", 413, "request_too_large")
    try:
        value = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise MobileError("A JSON object is required.") from None
    if not isinstance(value, dict):
        raise MobileError("A JSON object is required.")
    return value


def _only(payload, fields):
    if set(payload) - set(fields):
        raise MobileError("The request contains unsupported fields.")
    return payload


def _context(request):
    account = authenticate_request(request)
    if account is None:
        if session_unreadable(request):
            raise MobileError("Sign-in is temporarily unavailable.", 503, "auth_unavailable")
        raise MobileError("Please sign in.", 401, "unauthenticated")
    if account.role != "learner" or account.subject_type != "learner":
        raise MobileError("This service is for a learner's own account.", 403, "learner_only")
    if not getattr(account, "is_active", False):
        raise MobileError("Please sign in.", 401, "unauthenticated")
    try:
        source = EnrolmentUser.all_learners.get(pk=account.subject_id)
    except EnrolmentUser.DoesNotExist:
        raise MobileError("Learner not found.", 404, "learner_not_found") from None
    kind = str(source.learner_type or "").strip().lower()
    if kind not in {"apprenticeship", "commercial"}:
        raise MobileError("Your learner account needs its programme type confirmed.",
                          409, "learner_kind_unresolved")
    # Independent key sequences: NEVER substitute source.pk for a profile ID.
    # Fail closed on unlinked legacy profiles; the maintained LMS can repair them.
    profiles = list(LearnerProfile.objects.filter(enrolment_id=source.pk)
                    .only("id", "enrolment_id")[:2])
    if len(profiles) > 1:
        raise MobileError("Your learner profile needs an identity check.",
                          409, "learner_profile_ambiguous")
    return account, source, kind, profiles[0] if profiles else None


def _download_context(request):
    account = authenticate_request(request)
    if account is not None and not getattr(account, "is_active", False):
        raise MobileError("Please sign in.", 401, "unauthenticated")
    if staff_download_allowed(account):
        return account, None, None, None
    return _context(request)


def endpoint(method, *, context=None):
    def decorate(view):
        @csrf_exempt
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if request.method != method:
                    return _error("Method not allowed.", 405, "method_not_allowed")
                if not getattr(settings, "KBC_MOBILE_API_ENABLED", False):
                    return _error("The mobile connection is not enabled yet.", 503, "mobile_disabled")
                # Never enable this facade on a host whose usual auth gates are off.
                if (not auth_gate_enabled() or
                        os.environ.get("API_REQUIRE_AUTH", "1").strip().lower() in DISABLED):
                    return _error("The mobile connection is not available.", 503, "auth_gate_disabled")
                if method != "GET" and request.headers.get("X-Requested-With") != "XMLHttpRequest":
                    return _error("Missing request security header.", 403, "csrf")
                request.mobile_context = (context or _context)(request)
                response = view(request, *args, **kwargs)
                response.setdefault("Cache-Control", "private, no-store")
                return response
            except MobileError as exc:
                return _error(exc.message, exc.status, exc.code)
            except DatabaseError:
                return _error("The LMS is temporarily unavailable. Please try again.",
                              503, "lms_unavailable")
        return wrapped
    return decorate


def _require_profile(request):
    if request.mobile_context[3] is None:
        raise MobileError("Your learner profile must be linked before progress can be saved.",
                          409, "learner_profile_unlinked")


def _require_active_profile(request):
    _require_profile(request)
    _, source, _, profile = request.mobile_context
    # Canonical writers resolve active_only=True. An inactive linked profile
    # otherwise lets quiz grading return HTTP200 without saving its attempt.
    if not LearnerProfile.objects.filter(pk=profile.pk, enrolment_id=source.pk,
                                         lifecycle_status="active").exists():
        raise MobileError("Your learner profile must be active before study can be saved.",
                          409, "learner_profile_inactive")


def _detail(request):
    if not hasattr(request, "_mobile_detail"):
        _, source, _, _ = request.mobile_context
        # This is the maintained entitlement + content + progress projection.
        # It can hydrate the saved plan/refresh snapshots, like the LMS workspace.
        request._mobile_detail = build_learner_detail(source, source.pk, compact=True)
    return request._mobile_detail


def _component(request, component_id):
    # Do not guess by a title, a week number, a local ID or an origin ID.
    _require_active_profile(request)
    if not isinstance(component_id, str) or not ACTIVITY_ID.fullmatch(component_id):
        raise MobileError("A valid activity ID is required.")
    detail = _detail(request)
    if _learning_blocked(detail):
        raise MobileError("Your LMS learning access is not open yet.", 403, "learning_access_blocked")
    for component in _eligible_components(request):
        if component["componentId"] == component_id:
            return component
    raise MobileError("Activity not found in your learning plan.", 404, "activity_not_assigned")


def _learning_blocked(detail):
    return any(isinstance(detail.get(key), dict) and detail[key].get("blocked") is True
               for key in ("learningAccess", "accessGate"))


def _quiz_id(component):
    meta = component.get("quizMeta") or {}
    value = meta.get("quizId")
    if isinstance(value, bool):
        value = None
    try:
        value = int(value)
    except (ValueError, TypeError):
        raise MobileError("This activity has no published quiz.", 409, "quiz_unavailable") from None
    if value <= 0:
        raise MobileError("This activity has no published quiz.", 409, "quiz_unavailable")
    return value


def _assigned_courses(request):
    """Canonical effective plan IDs, including assigned courses with no media.

    Titles are display data only. An empty explicit plan must never inherit
    entitlement from a similarly named module or a saved progress record.
    """
    if not hasattr(request, "_mobile_courses"):
        detail = _detail(request)
        source = request.mobile_context[1]
        courses = {}
        for row in effective_training_plan(source):
            if not isinstance(row, dict):
                continue
            mid = row.get("moduleId")
            if not isinstance(mid, str) or not mid.strip():
                continue
            courses.setdefault(mid, {"moduleId": mid,
                "title": row.get("moduleTitle") or row.get("module") or mid})
        for row in detail.get("week", []) + detail.get("components", []):
            if row.get("moduleId") in courses and row.get("module"):
                courses[row["moduleId"]]["title"] = row["module"]
        request._mobile_courses = list(courses.values())
    return request._mobile_courses


def _published_choice_quiz_ids(quiz_ids):
    """One read of publication/type metadata; never load or expose answer keys."""
    if not quiz_ids:
        return set()
    with connections["enrolment"].cursor() as cursor:
        cursor.execute("""SELECT q.id FROM curriculum.quizzes q
            WHERE q.id=ANY(%s) AND lower(COALESCE(q.status, ''))='published'
              AND EXISTS (SELECT 1 FROM curriculum.quiz_questions x
                          WHERE x.quiz_id=q.id AND x.is_archived=false)
              AND NOT EXISTS (SELECT 1 FROM curriculum.quiz_questions x
                  WHERE x.quiz_id=q.id AND x.is_archived=false AND (
                    COALESCE(x.question_type, '') <> ALL(%s)
                    OR NOT EXISTS (SELECT 1 FROM curriculum.quiz_answers a WHERE a.question_id=x.id)))""",
            [sorted(quiz_ids), sorted(CHOICE_QUESTION_TYPES)])
        return {row[0] for row in cursor.fetchall()}


def _media_url(value):
    return isinstance(value, str) and bool(re.match(r"^(?:https?://[^/\s]+|/(?!/))", value.strip(), re.I))


def _eligible_components(request):
    """Use the same server-resolved mobile entitlement for lists and actions."""
    if hasattr(request, "_mobile_components"):
        return request._mobile_components
    detail = _detail(request)
    if _learning_blocked(detail):
        return []
    modules = {row["moduleId"] for row in _assigned_courses(request)}
    candidates, quiz_ids = [], set()
    for row in detail.get("components", []):
        if not isinstance(row, dict) or row.get("moduleId") not in modules:
            continue
        kind = str(row.get("type") or "").strip().lower().replace("-", "_")
        # Assignments and live sessions have separate maintained workflows;
        # an accidental attached URL/quiz must not make them mobile activities.
        if kind not in MEDIA_COMPONENT_TYPES | {"quiz", ""}:
            continue
        if row.get("isQuiz"):
            try:
                qid = _quiz_id(row)
            except MobileError:
                continue
            quiz_ids.add(qid)
            candidates.append((row, "quiz", qid))
        elif kind in MEDIA_COMPONENT_TYPES:
            mode = "audio" if _media_url(row.get("audioUrl")) else "video" if _media_url(row.get("videoUrl")) else None
            if mode:
                candidates.append((row, mode, None))
    published = _published_choice_quiz_ids(quiz_ids)
    by_id, ambiguous = {}, set()
    for row, mode, qid in candidates:
        if qid is not None and qid not in published:
            continue
        cid = row.get("componentId")
        standalone = qid is not None and not cid
        if standalone:
            identity = json.dumps([row["moduleId"], row.get("weekId"), qid], separators=(",", ":"))
            cid = "mobile-quiz-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if not isinstance(cid, str) or not ACTIVITY_ID.fullmatch(cid):
            continue
        item = {**row, "componentId": cid, "mobileMode": mode,
                "_standaloneQuiz": standalone}
        previous = by_id.get(cid)
        if previous and (previous["moduleId"], previous.get("weekId")) != (row["moduleId"], row.get("weekId")):
            ambiguous.add(cid)
        by_id[cid] = item
    request._mobile_components = [row for cid, row in by_id.items() if cid not in ambiguous]
    return request._mobile_components


def _selected_module(request):
    ids = [row["moduleId"] for row in _assigned_courses(request)]
    selected = request.GET.get("moduleId")
    if selected is not None:
        if selected not in ids:
            raise MobileError("This course is not assigned to your account.", 404, "module_not_assigned")
        return selected
    return PILOT_MODULE_ID if PILOT_MODULE_ID in ids else ids[0] if ids else None


def _mobile_progress(request, module_id):
    detail = _detail(request)
    return project_progress({**detail, "components": _eligible_components(request)}, module_id)


def _presentation_metadata(components):
    """Read only whitelisted presentation fields for already-entitled IDs."""
    eligible = {row["componentId"]: row for row in components
                if row.get("componentId") and not row.get("_standaloneQuiz")}
    if not eligible:
        return {}
    with connections["enrolment"].cursor() as cursor:
        cursor.execute("""SELECT c.id, c.week_id, c.display_order,
            c.settings_json->'teachingWeek', c.settings_json->'pointNumber',
            c.settings_json->>'bookNarrationAssetId', c.settings_json->'sourceReadingComponentIds',
            (jsonb_typeof(c.settings_json->'transcript') = 'string'
             AND length(btrim(c.settings_json->>'transcript')) > 0),
            c.settings_json->'legacySettings', c.settings_json->>'requirement',
            jsonb_build_object('teachingWeek', c.settings_json->'teachingWeek',
                'pointNumber', c.settings_json->'pointNumber',
                'sourceReadingComponentIds', c.settings_json->'sourceReadingComponentIds',
                'required', c.settings_json->'required',
                'optional', c.settings_json->'optional',
                'countsTowardsRequiredCompletion', c.settings_json->'countsTowardsRequiredCompletion',
                'required_route_minutes', c.settings_json->'required_route_minutes',
                'required_weekly_minutes', c.settings_json->'required_weekly_minutes',
                'full_content_planned_minutes', c.settings_json->'full_content_planned_minutes',
                'plannedLearningMinutes', c.settings_json->'plannedLearningMinutes',
                'source_content_type', c.settings_json->'source_content_type',
                'parent_activity_id', c.settings_json->'parent_activity_id'), c.module_catalogue_id,
            w.week_number, w.display_order
            FROM curriculum.components c
            JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
            JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
            WHERE c.module_catalogue_id=ANY(%s) AND c.id=ANY(%s)
              AND """ + visible_child_sql("c") + """
              AND """ + visible_week_sql("w") + """
              AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent, '') <> '')""",
            [list(dict.fromkeys(row["moduleId"] for row in eligible.values())), list(eligible)])
        rows = cursor.fetchall()
    result = {}
    for cid, week_id, order, teaching_week, point_number, asset_id, reading_ids, has_transcript, legacy, requirement, direct, module_id, week_number, week_order in rows:
        component = eligible.get(cid)
        if component is None or component.get("weekId") != week_id or component["moduleId"] != module_id:
            continue
        item = {"displayOrder": order}
        # Native saves store legacySettings as a JSON string. The raw JSONB
        # text loader can add one transport layer around that string.
        legacy = presentation_object(legacy, max_layers=2)
        direct = presentation_object(direct)
        # Decode scalar fields inside a JSON object: raw JSONB text '1' is
        # otherwise indistinguishable from an already-decoded authored "1".
        # A numeric JSON value stays numeric; an authored string stays a string.
        teaching_week = direct.get("teachingWeek", teaching_week)
        point_number = direct.get("pointNumber", point_number)
        reading_ids = direct.get("sourceReadingComponentIds", reading_ids)
        authored = {**legacy, **{key: value for key, value in direct.items() if value is not None}}
        route = {}
        for key in ("required", "optional", "countsTowardsRequiredCompletion"):
            if type(authored.get(key)) is bool:
                route[key] = authored[key]
        for source, target in (("required_route_minutes", "requiredRouteMinutes"),
                               ("required_weekly_minutes", "requiredWeeklyMinutes"),
                               ("full_content_planned_minutes", "fullContentPlannedMinutes"),
                               ("plannedLearningMinutes", "plannedLearningMinutes")):
            value = authored.get(source)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                route[target] = value
        authored_requirement = requirement or legacy.get("requirement")
        if authored_requirement in ("Required", "Optional"):
            route["requirement"] = authored_requirement
        if route:
            item["learningRoute"] = {"source": "canonical_settings", **route}
        for source, target in (("source_content_type", "sourceContentType"),
                               ("parent_activity_id", "sourceParentActivityId")):
            value = authored.get(source)
            if isinstance(value, str) and 0 < len(value) <= 180:
                item[target] = value
        if teaching_week is None:
            teaching_week = legacy.get("teachingWeek")
        if point_number is None:
            point_number = legacy.get("pointNumber")
        for key, value in (("teachingWeek", teaching_week), ("pointNumber", point_number),
                           ("weekNumber", week_number), ("weekDisplayOrder", week_order)):
            valid = type(value) is int and value >= 0
            if key == "pointNumber":
                valid = valid and 0 < value <= 9007199254740991
            if valid:
                item[key] = value
        if isinstance(asset_id, str) and asset_id:
            item["bookNarrationAssetId"] = asset_id
        if (isinstance(reading_ids, list) and all(isinstance(value, str) and
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}", value) for value in reading_ids)):
            item["sourceReadingComponentIds"] = reading_ids
        item["transcriptAvailable"] = bool(component.get("audioUrl") and has_transcript)
        if item["transcriptAvailable"]:
            item["transcriptPath"] = f"{BASE}components/{cid}/transcript/"
        result[cid] = item
    return result


def _delegate(request, path, view, *, payload=None, query=None, **kwargs):
    account, source, kind, _ = request.mobile_context
    blocked = refusal_for(path, account, method=request.method)
    if blocked is not None:
        return blocked
    if request.method == "POST" and path.startswith(("/learner_api/time-tracking/",
            "/learner_api/components/", "/learner_api/videos/", "/learner_api/quizzes/")):
        # Recheck after detail hydration and directly before the canonical writer.
        _require_active_profile(request)
    child = copy.copy(request)
    child.path = child.path_info = path
    # Never forward arbitrary query selectors, alternate learners or audit sources.
    child.GET = request.GET.copy()
    child.GET.clear()
    for key, value in (query or {}).items():
        child.GET[key] = str(value)
    child.GET["kind"] = kind
    child.GET["learnerId"] = str(source.pk)
    if payload is not None:
        child._body = json.dumps(payload, allow_nan=False).encode("utf-8")
        child._stream = io.BytesIO(child._body)
        child.META = request.META.copy()
        child.META["CONTENT_TYPE"] = "application/json"
        child.META["CONTENT_LENGTH"] = str(len(child._body))
    return view(child, **kwargs)


def _canonical_metadata(component):
    return {
        "module": component.get("module"), "week": component.get("week"),
        "componentTitle": component.get("component"),
        "componentType": component.get("type"),
        "ksbs": [str(item["code"]) for item in (component.get("ksbMappings") or [])
                 if isinstance(item, dict) and item.get("code")],
    }


def _completion_payload(request, component, activity_kind, activity_id):
    from .time_tracking import verify_tracking_session, TrackingSessionError
    incoming = _only(_body(request), COMPLETION_FIELDS)
    if activity_kind != "quiz" and "answers" in incoming:
        raise MobileError("Answers are only accepted for a quiz.")
    claimed = incoming.get("timeTakenSeconds")
    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
        raise MobileError("The active duration must be a non-negative number.")
    if not math.isfinite(claimed) or claimed < 0:
        raise MobileError("The active duration must be a non-negative number.")
    _, source, kind, _ = request.mobile_context
    try:
        tracking = verify_tracking_session(
            incoming.get("trackingToken"), activity_kind=activity_kind,
            activity_id=activity_id, learner_kind=kind, learner_id=source.pk,
            claimed_seconds=claimed,
        )
    except TrackingSessionError as exc:
        raise MobileError(str(exc), 400, "invalid_tracking_session") from None
    expected_mode = "active_quiz" if activity_kind == "quiz" else "active_playback"
    if tracking["countingMode"] != expected_mode:
        raise MobileError("This timing session is not for the selected activity.",
                          400, "invalid_tracking_mode")
    # The canonical generic/video APIs also support manually entered hours.
    # This facade supports measured online activity only: it must not turn an
    # inflated client duration into accepted selected-time totals.
    payload = {**incoming, **_canonical_metadata(component)}
    payload["timeTakenSeconds"] = tracking["verifiedSeconds"]
    payload["timeEntrySource"] = "timer"
    payload["reportedTime"] = f'{tracking["verifiedSeconds"] / 60} minutes'
    if activity_kind == "video":
        payload["videoTitle"] = component.get("component")
    return payload


@endpoint("GET", context=_download_context)
def download_android(request):
    """Deliver the installer to entitled learners, active admins and coaches."""
    if request.mobile_context[0].role == "learner":
        _require_active_profile(request)
        detail = _detail(request)
        if _learning_blocked(detail):
            raise MobileError("Your LMS learning access is not open yet.", 403, "learning_access_blocked")
        if not _assigned_courses(request):
            raise MobileError("No courses are assigned to your account.", 403, "module_not_assigned")
    try:
        apk = open(ANDROID_APK_PATH, "rb")
    except OSError:
        raise MobileError("The Android download is not available yet.", 503, "download_unavailable") from None
    return installer_response(request, apk)


@endpoint("GET")
def session(request):
    from .mobile_rewards import enabled as rewards_enabled
    account, source, kind, profile = request.mobile_context
    reward_ids = [] if _learning_blocked(_detail(request)) else [c['moduleId'] for c in _assigned_courses(request) if rewards_enabled(module_id=c['moduleId'])]
    shared_rewards_enabled=bool(reward_ids)
    return JsonResponse({
        "authenticated": True,
        "rewardModuleIds": reward_ids,
        "user": {"id": str(source.pk), "accountId": str(account.pk),
                 "displayName": account.display_name, "learnerKind": kind,
                 "learnerProfileId": str(profile.pk) if profile else None},
        "capabilities": {**CAPABILITIES,
            "customPointsPolicy":shared_rewards_enabled,"timeObservations":shared_rewards_enabled,
            "crossDeviceTimerDeduplication":shared_rewards_enabled,"calendarWrite": bool(
            getattr(settings, "KBC_MOBILE_CALENDAR_WRITES_ENABLED", False))},
    })


@endpoint("GET")
def catalogue(request):
    _require_active_profile(request)
    detail = _detail(request)
    courses = _assigned_courses(request)
    course_ids = {course["moduleId"] for course in courses}
    blocked = _learning_blocked(detail)
    scoped = _eligible_components(request)
    presentation = _presentation_metadata(scoped)
    from .mobile_rewards import enabled as rewards_enabled
    reward_modules = {cid:rewards_enabled(module_id=cid) for cid in course_ids}
    components = []
    for row in ([] if blocked else scoped):
        item = {key: value for key, value in row.items() if key in COMPONENT_FIELDS}
        item.update(presentation.get(row.get("componentId"), {}))
        meta = item.get("quizMeta")
        if isinstance(meta, dict):
            item["quizMeta"] = {key: meta[key] for key in ("quizId", "duration", "timeUnit") if key in meta}
            if isinstance(meta.get("questions"), int) and not isinstance(meta.get("questions"), bool):
                item["quizMeta"]["questions"] = meta["questions"]
        item["mobileMode"] = row["mobileMode"]
        # Linked mixed content still has one canonical activity/mode; a quiz
        # does not expose incidental document, session or media attachments.
        for field, mode in (("audioUrl", "audio"), ("videoUrl", "video")):
            if item["mobileMode"] != mode:
                item.pop(field, None)
        if item["mobileMode"] != "audio":
            item.pop("alternativeYouTubeUrl", None)
        item["mechanicalNarrationAllowed"] = False
        item["rewardObservationsSupported"] = reward_modules[row['moduleId']]
        components.append(item)
    return JsonResponse({
        "schemaVersion": 2, "source": "lms", "catalogueScope": "assigned",
        "courses": [{**course, "availableActivityCount": sum(
            row["moduleId"] == course["moduleId"] for row in scoped)} for course in courses],
        # Kept only for older PMP clients. Authorization uses the effective plan.
        "moduleScope": {"moduleId": PILOT_MODULE_ID, "title": PILOT_MODULE_TITLE,
                        "assigned": PILOT_MODULE_ID in course_ids},
        "modules": list(dict.fromkeys(course["title"] for course in courses)),
        "weeks": [{key: row[key] for key in ("moduleId", "module", "weekId", "week") if key in row}
                  for row in detail.get("week", []) if row.get("moduleId") in course_ids],
        "components": components,
        "learningAccess": detail.get("learningAccess"), "accessGate": detail.get("accessGate"),
    })


@endpoint("GET")
def transcript(request, component_id):
    component = _component(request, component_id)
    if component["mobileMode"] != "audio":
        raise MobileError("This activity has no published audio transcript.", 404, "transcript_unavailable")
    blocked = refusal_for(f"/learner_api/components/{component_id}/transcript/",
                          request.mobile_context[0], method="GET")
    if blocked is not None:
        return blocked
    with connections["enrolment"].cursor() as cursor:
        cursor.execute("""SELECT c.settings_json->>'transcript'
            FROM curriculum.components c
            JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
            JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
            WHERE c.id=%s AND c.module_catalogue_id=%s AND c.week_id=%s
              AND jsonb_typeof(c.settings_json->'transcript')='string'
              AND """ + visible_child_sql("c") + """
              AND """ + visible_week_sql("w") + """
              AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent, '') <> '')""",
            [component_id, component["moduleId"], component.get("weekId")])
        row = cursor.fetchone()
    if not row or not isinstance(row[0], str) or not row[0].strip():
        raise MobileError("A transcript is not available for this recording.", 404, "transcript_unavailable")
    return JsonResponse({"source": "lms", "componentId": component_id, "transcript": row[0]})


@endpoint("GET")
def progress(request):
    _require_active_profile(request)
    detail = _detail(request)
    module_id = _selected_module(request)
    projection, rows = _mobile_progress(request, module_id)
    return JsonResponse({"schemaVersion": 1, "source": "lms",
                         **{key: value for key, value in detail.items() if key in PROGRESS_FIELDS},
                         **projection, "moduleId": module_id,
                         "mobileMeasuredTime": measured_time(rows, module_id)})


@endpoint("GET")
def hours(request):
    from .overview_week import overview_week
    _require_active_profile(request)
    module_id = _selected_module(request)
    _, source, kind, _ = request.mobile_context
    response = _delegate(request, f"/learner_api/overview-week/{kind}/{source.pk}/",
                         overview_week, query={"section": "home"}, kind=kind, pk=source.pk)
    if response.status_code != 200:
        return response
    data = json.loads(response.content)
    _, rows = _mobile_progress(request, module_id)
    return JsonResponse({**data, "source": "lms", "moduleId": module_id,
                         "canonicalProgrammeOtjh": data.get("otjh"),
                         "mobileMeasuredTime": measured_time(rows, module_id)})


@endpoint("GET")
def points(request):
    from .rewards_summary import learner_rewards_summary
    _, source, kind, _ = request.mobile_context
    return _delegate(request, f"/learner_api/rewards-summary/{kind}/{source.pk}/",
                     learner_rewards_summary, kind=kind, pk=source.pk)


@endpoint("GET")
def meetings(request):
    from .calendar import learner_calendar
    _, source, kind, _ = request.mobile_context
    return _delegate(request, f"/learner_api/calendar/{kind}/{source.pk}/",
                     learner_calendar, kind=kind, pk=source.pk)


@endpoint("POST")
def start_learning_session(request):
    from .time_tracking import start_time_tracking
    _require_active_profile(request)
    payload = _only(_body(request), {"componentId", "mode"})
    component = _component(request, payload.get("componentId"))
    mode = payload.get("mode")
    if mode != component["mobileMode"]:
        raise MobileError("This activity has no published media for that mode.", 409, "media_unavailable")
    if mode == "quiz":
        activity_kind, activity_id, counting = "quiz", _quiz_id(component), "active_quiz"
        completion = f'{BASE}quizzes/{component["componentId"]}/submit/'
    elif mode == "audio" and component.get("audioUrl"):
        activity_kind, activity_id, counting = "component", component["componentId"], "active_playback"
        completion = f'{BASE}components/{component["componentId"]}/complete/'
    elif mode == "video" and component.get("videoUrl"):
        activity_kind, activity_id, counting = "video", component["componentId"], "active_playback"
        completion = f'{BASE}videos/{component["componentId"]}/complete/'
    else:
        raise MobileError("This activity has no published media for that mode.",
                          409, "media_unavailable")
    response = _delegate(request, "/learner_api/time-tracking/start/", start_time_tracking,
                         payload={"activityKind": activity_kind, "activityId": str(activity_id),
                                  "countingMode": counting})
    if response.status_code != 200:
        return response
    data = json.loads(response.content)
    return JsonResponse({**data, "activityKind": activity_kind, "activityId": str(activity_id),
                         "componentId": component["componentId"], "completionPath": completion})


@endpoint("POST")
def complete_component(request, component_id):
    from .components import submit_component_progress
    _require_active_profile(request)
    component = _component(request, component_id)
    if component["mobileMode"] != "audio":
        raise MobileError("This activity has no published audio.", 409, "audio_unavailable")
    payload = _completion_payload(request, component, "component", component_id)
    return _delegate(request, f"/learner_api/components/{component_id}/complete/",
                     submit_component_progress, payload=payload, component_id=component_id)


@endpoint("POST")
def complete_video(request, component_id):
    from .videos import submit_video_progress
    _require_active_profile(request)
    component = _component(request, component_id)
    if component["mobileMode"] != "video":
        raise MobileError("This activity has no published video.", 409, "video_unavailable")
    payload = _completion_payload(request, component, "video", component_id)
    return _delegate(request, f"/learner_api/videos/{component_id}/complete/",
                     submit_video_progress, payload=payload, component_id=component_id)


def _validated_quiz_answers(quiz, submitted):
    """Reject stale/mis-mapped native labels before they consume an attempt.

    Native A-E labels are presentation only. The LMS expects a canonical numeric
    option ID for single choice, and a list of IDs for multiple choice.
    """
    if not isinstance(submitted, dict):
        raise MobileError("Quiz answers must be keyed by question ID.")
    questions = (quiz or {}).get("questions")
    if not isinstance(questions, list) or not questions:
        raise MobileError("This quiz has no published questions.", 409, "quiz_unavailable")
    questions_by_id = {str(q["id"]): q for q in questions}
    if len(questions_by_id) != len(questions) or set(submitted) != set(questions_by_id):
        raise MobileError("Your answers do not match the current quiz. Reload it before submitting.",
                          409, "quiz_answers_mismatch")
    normalised = {}
    for question_id, question in questions_by_id.items():
        qtype = question.get("type")
        if qtype not in CHOICE_QUESTION_TYPES:
            raise MobileError("This question format is not supported in the mobile app yet.",
                              409, "quiz_type_unsupported")
        value = submitted[question_id]
        if qtype == "multiple_choice":
            if not isinstance(value, list) or not value:
                raise MobileError("Choose valid answers for every question.", 400, "invalid_answer_ids")
            selected = value
        else:
            if isinstance(value, (list, dict)):
                raise MobileError("A single-choice answer must contain one canonical option ID.",
                                  400, "invalid_answer_ids")
            selected = [value]
        allowed = {str(answer["id"]) for answer in question.get("answers", [])}
        resolved = []
        for answer_id in selected:
            # bool is an int subclass; accepting True would accidentally select ID 1.
            if (isinstance(answer_id, bool) or not isinstance(answer_id, (int, str))
                    or not re.fullmatch(r"[1-9][0-9]*", str(answer_id))
                    or str(answer_id) not in allowed):
                raise MobileError("A quiz answer is not a current option ID. Reload the quiz.",
                                  409, "quiz_answer_mapping_required")
            resolved.append(int(answer_id))
        if len(set(resolved)) != len(resolved):
            raise MobileError("Choose each answer only once.", 400, "invalid_answer_ids")
        normalised[question_id] = resolved if qtype == "multiple_choice" else resolved[0]
    return normalised


@endpoint("GET")
def quiz(request, component_id):
    from .quizzes import quiz_detail
    component = _component(request, component_id)
    if component["mobileMode"] != "quiz":
        raise MobileError("This activity has no published quiz.", 409, "quiz_unavailable")
    quiz_id = _quiz_id(component)
    return _delegate(request, f"/learner_api/quizzes/{quiz_id}/", quiz_detail, quiz_id=quiz_id)


@endpoint("POST")
def submit_quiz(request, component_id):
    from .quizzes import submit_quiz_attempt, _fetch_quiz
    _require_active_profile(request)
    component = _component(request, component_id)
    if component["mobileMode"] != "quiz":
        raise MobileError("This activity has no published quiz.", 409, "quiz_unavailable")
    quiz_id = _quiz_id(component)
    payload = _completion_payload(request, component, "quiz", quiz_id)
    payload["answers"] = _validated_quiz_answers(_fetch_quiz(quiz_id), payload.get("answers"))
    return _delegate(request, f"/learner_api/quizzes/{quiz_id}/submit/",
                     submit_quiz_attempt, payload=payload, quiz_id=quiz_id)


@endpoint("POST")
def meeting_action(request, action):
    from . import calendar
    if not getattr(settings, "KBC_MOBILE_CALENDAR_WRITES_ENABLED", False):
        raise MobileError("Meeting changes are not enabled in the mobile app yet.",
                          409, "calendar_writes_disabled")
    handlers = {"book": calendar.learner_calendar_book,
                "reschedule": calendar.learner_calendar_reschedule,
                "cancel": calendar.learner_calendar_cancel}
    if action not in handlers:
        raise MobileError("Meeting action not found.", 404, "not_found")
    payload = _body(request)
    # Existing booking handlers validate dates, event ownership, availability,
    # idempotency and notifications. They remain the sole meeting writer.
    if any(key in payload for key in ("learnerId", "learner_id", "kind", "profileId",
                                     "learnerProfileId", "accountId", "subjectId")):
        raise MobileError("Meeting identity is taken from your signed-in account.")
    _, source, kind, _ = request.mobile_context
    return _delegate(request, f"/learner_api/calendar/{kind}/{source.pk}/{action}/",
                     handlers[action], payload=payload, kind=kind, pk=source.pk)
