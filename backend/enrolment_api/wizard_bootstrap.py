"""Everything the enrolment wizard needs to open, in one response.

GET /enrolment_api/wizard-bootstrap/<kind>/<id>/  -> {board, ilr, ksbProfile}

The wizard cannot render until it has the learner's board, their saved Extended
ILR *and* their programme's KSB profile, and it fetched them as separate
round-trips — on a slow link that is several waits before the first useful
paint, and each only started after the component that needs it had mounted.

The KSB profile is the third of those, and it was the worst: it could not even
begin until the ILR had arrived (the wizard seeds its unrated rows only after
hydration), so it was strictly serialized behind the others. Until it landed the
Skills Radar had no rows, which made the step look complete and the progress
rail count it — the flicker this collapses. Included here because the programme
it is keyed on is already known while the board is being built.

Composed from the same builders the individual endpoints use (`to_board`,
`read_extended_ilr`, `ksb_profile_for_programme`) rather than re-deriving any
shape here: those endpoints stay the source of truth and are still served for
every other caller, so there is one place to change when a payload changes.
"""
from django.db import DatabaseError
from django.http import JsonResponse

from learner_api.curriculum import ksb_profile_for_programme
from learner_api.learner_progression import advance_learner
from learner_api.mappers import to_board
from learner_api.models import CommercialUser, EnrolmentUser

from .auth import enrolment_login_required
from .extended_ilr import read_extended_ilr

KINDS = {"apprenticeship": EnrolmentUser, "commercial": CommercialUser}


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


@enrolment_login_required
def wizard_bootstrap(request, kind, learner_id):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'. Expected one of: {', '.join(sorted(KINDS))}.", 400)

    try:
        # all_learners, not objects: `objects` is scoped to apprenticeship rows,
        # and ids are unique across the single table, so a commercial learner
        # would 404 here otherwise. Mirrors enrolment_user_detail.
        learner = EnrolmentUser.all_learners.filter(pk=learner_id).first()
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if learner is None:
        return _error("Learner not found.", 404)

    try:
        # GET on enrolment_user_detail is not a pure read: it re-checks whether a
        # learner whose reviews are all signed belongs in Delivery, and advances
        # their stage. Callers replacing that request with this one must still
        # get the healing, or a learner promoted by nothing else would stall.
        from learner_api.learning_plan import promote_learner_if_ready

        learner_type = str(getattr(learner, "learner_type", "") or "").strip() or "apprenticeship"
        if promote_learner_if_ready(learner_type, learner.pk):
            learner.refresh_from_db()
        advance_learner(learner)

        board = to_board(learner)
        ilr = read_extended_ilr(kind, int(learner_id), str(learner.username or "").strip())

        # Keyed on the programme the board just resolved. A learner with no
        # programme has no profile to fetch, and the helper already answers
        # (None, []) for one that has nothing authored — so this never fails a
        # bootstrap that would otherwise have succeeded.
        programme = str((board.get("programme") or {}).get("name") or "").strip()
        if programme:
            standard, ksb_results = ksb_profile_for_programme(programme)
            ksb_profile_payload = {"standard": standard, "results": ksb_results}
        else:
            ksb_profile_payload = None
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({"board": board, "ilr": ilr, "ksbProfile": ksb_profile_payload})
