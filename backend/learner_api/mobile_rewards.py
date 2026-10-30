"""Authenticated course reward adapter; all money/points remain in Engagement."""
from django.conf import settings
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone
from . import mobile


def enabled(request=None, module_id=None):
    if getattr(settings,"KBC_SHARED_REWARDS_READY",False) is not True:
        return False
    if request is not None:
        detail=mobile._detail(request)
        if mobile._learning_blocked(detail):
            return False
        module_id = module_id or mobile._selected_module(request)
        if not module_id:
            return False
    try:
        from engagement_api.shared_rewards import policy
        with connections["default"].cursor() as cursor:
            cfg=policy(cursor,module_id or mobile.PILOT_MODULE_ID,require=False)
            return bool(cfg and cfg["enabled"] and cfg["starts_at"] and cfg["starts_at"]<=timezone.now())
    except DatabaseError:
        return False


def _scope(request,write=False,module_id=None):
    if write:
        mobile._require_active_profile(request)
    else:
        mobile._require_profile(request)
    detail=mobile._detail(request)
    if mobile._learning_blocked(detail):
        raise mobile.MobileError("Your learning access is not open yet.",403,"learning_access_blocked")
    module_id = module_id or mobile._selected_module(request)
    if not module_id or module_id not in {c['moduleId'] for c in mobile._assigned_courses(request)}:
        raise mobile.MobileError("This module is not assigned to your account.",404,"module_not_assigned")
    if not enabled(module_id=module_id):
        raise mobile.MobileError("The points policy is not enabled yet.",409,"rewards_not_enabled")
    return request.mobile_context


def _call(function,**kwargs):
    from engagement_api.segment_rules import RewardError
    try:
        return JsonResponse(function(**kwargs))
    except RewardError as exc:
        raise mobile.MobileError("This request could not be saved. "+exc.code.replace("_"," ")+".",
                                  exc.status,exc.code) from None


@mobile.endpoint("GET")
def summary(request):
    from engagement_api.shared_rewards import summary as service
    _,source,_,_=_scope(request)
    return _call(service,learner_id=str(source.pk),module_id=mobile._selected_module(request))


@mobile.endpoint("POST")
def observations(request):
    from engagement_api.shared_rewards import record_observations
    from .time_tracking import verify_tracking_session,TrackingSessionError
    body=mobile._only(mobile._body(request),
        {"trackingToken","componentId","mode","eventId","sequence","intervals"})
    component=mobile._component(request,body.get("componentId"))
    _,source,kind,profile=_scope(request,write=True,module_id=component.get('moduleId'))
    if not enabled(module_id=component.get('moduleId')):
        raise mobile.MobileError("This course has no active mobile rewards policy.",404,"module_not_assigned")
    mode=body.get("mode")
    if mode != component.get("mobileMode"):
        raise mobile.MobileError("The selected activity has no media for this mode.",409,"media_unavailable")
    if mode=="audio" and component.get("audioUrl"):
        activity_kind,activity_id,counting="component",component["componentId"],"active_playback"
    elif mode=="video" and component.get("videoUrl"):
        activity_kind,activity_id,counting="video",component["componentId"],"active_playback"
    elif mode=="quiz":
        activity_kind,activity_id,counting="quiz",mobile._quiz_id(component),"active_quiz"
    else:
        raise mobile.MobileError("The selected activity has no media for this mode.",409,"media_unavailable")
    token=body.get("trackingToken")
    if not isinstance(token,str) or not token or len(token)>8192:
        raise mobile.MobileError("A signed timing token is required.",400,"invalid_tracking_session")
    try:
        tracking=verify_tracking_session(token,activity_kind=activity_kind,activity_id=activity_id,
            learner_kind=kind,learner_id=source.pk,claimed_seconds=0)
    except TrackingSessionError:
        raise mobile.MobileError("The signed timing token does not match this activity.",400,"invalid_tracking_session") from None
    if tracking["countingMode"]!=counting:
        raise mobile.MobileError("This timing mode is not supported.",400,"invalid_tracking_mode")
    mobile._require_active_profile(request)
    return _call(record_observations,learner_id=str(source.pk),profile_id=profile.pk,
        component=component,tracking={**tracking,"activityKind":activity_kind,"activityId":str(activity_id),"learnerKind":kind},
        token=token,event_id=body.get("eventId"),sequence=body.get("sequence"),
        observed_intervals=body.get("intervals"))


@mobile.endpoint("POST")
def voucher_request(request):
    from engagement_api.shared_rewards import voucher_request as service
    account,source,_,_=_scope(request,write=True)
    body=mobile._only(mobile._body(request),{"requestId","amountGbp"})
    mobile._require_active_profile(request)
    return _call(service,learner_id=str(source.pk),learner_name=account.display_name or "",
        request_id=body.get("requestId"),amount_gbp=body.get("amountGbp"),module_id=mobile._selected_module(request))


@mobile.endpoint("POST")
def leaderboard(request):
    from engagement_api.shared_rewards import set_leaderboard
    _,source,_,_=_scope(request,write=True)
    body=mobile._only(mobile._body(request),{"optedIn","displayName"})
    mobile._require_active_profile(request)
    return _call(set_leaderboard,learner_id=str(source.pk),module_id=mobile._selected_module(request),
        opted_in=body.get("optedIn"),display_name=body.get("displayName"))
