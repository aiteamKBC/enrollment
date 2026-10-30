"""Read-only learner-detail lookup for the learner workspace page.

    GET /learner_api/learner-detail/<kind>/<int:pk>/

`kind` is 'commercial' or 'apprenticeship', matching the vocabulary already
used by the /training-plan/:kind/:userId route. Combines the learner's own
record (CommercialUser / EnrolmentUser) with its "Learner"."Active_users"
mirror (present only while the learner is Active) into one response shaped by
mappers.to_learner_detail, then annotates each saved component with its
authored expected_otjh (curriculum.components) and a
programme-wide total.
authored expected_otjh (curriculum.components) and a programme-wide total.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from django.db import DatabaseError, connections
from django.db.models import prefetch_related_objects
from django.http import JsonResponse
from .projection_performance import measure_projection
from django.utils import timezone

from login.permissions import learner_self_or_staff
from old_otjh.service import ServiceError
from curriculum_api.session_component_identity import occurrence_fallbacks as _live_session_occurrence_fallbacks

from .active_users import completed_hours_from_progress, fmt_hours, hydrate_source_training_plan, target_by_elapsed_time, week_by_elapsed_time
from .identity import learner_profile_for_source
from .aptem_status import programme_status
from .constants import DEFAULT_PROGRAMME_STATUS
from .learner_progression import access_gate, advance_learner
from .learning_plan import effective_training_plan
from .programme_access import learning_access
from .mappers import _s, get_training_plan, to_learner_detail
from .models import EnrolmentUser, LearnerProfile
from .retained_quiz_progress import retain_quiz_progress
from .student_activity_access import student_activity_available
from curriculum_api.week_visibility import visible_child_sql, visible_week_sql

logger = logging.getLogger(__name__)

#: An uploaded file only counts as a component's audio when it is one of these.
AUDIO_FILE_SUFFIXES = {'.mp3', '.wav', '.ogg', '.oga', '.m4a', '.aac', '.flac', '.opus', '.wma'}

# Programme-wide portfolio templates. These live in the curriculum uploads
# container and are deliberately defaults rather than authored component data:
# a tutor can still attach a more specific brief to an individual assignment,
# while every otherwise-empty assignment gives the learner the correct monthly
# workbook to download, complete, and upload as evidence.
PROGRAMME_ASSIGNMENT_TEMPLATES = {
    "pcp": {
        "fileName": "PCP.docx",
        "resourceUrl": "/curriculum_api/curriculum/uploads/assignment-templates/PCP.docx",
    },
    "mm": {
        "fileName": "MM.docx",
        "resourceUrl": "/curriculum_api/curriculum/uploads/assignment-templates/MM.docx",
    },
    "me": {
        "fileName": "ME.docx",
        "resourceUrl": "/curriculum_api/curriculum/uploads/assignment-templates/ME.docx",
    },
}

ASSIGNMENT_MODULE_TEMPLATE_IDS = {
    "me": {
        "MOD-202608228DDFCB53074A",
        "MOD-2026082243BD5ED0A8EA",
        "MOD-2026082273BF1B44335F",
    },
    "mm": {
        "MOD-202608223E23693425BC",
        "MOD-20260822222D7B9190AA",
        "MOD-20260822BFA56444DE10",
        "MOD-AI-IN-MARKETING-MM",
    },
    "pcp": {
        "MOD-2026082245779A87FE0C",
        "MOD-20260822B2177D2C4599",
        "MOD-202608223894BBCBCF5F",
        "MOD-202608227739EC14E0CC",
        "MOD-202608226F0A69EDAD30",
        "MOD-20260822007072C8A616",
        "MOD-2026082281333774FD28",
        "MOD-20260822C8C4CF8F9D6F",
        # The existing PMI SP module used by Mohamed El Masry's original plan.
        "MOD-202608220A1D2C26A3E5",
    },
}


def _programme_assignment_template(programme):
    """Return the portfolio workbook for one of the three taught programmes."""
    normalised = re.sub(r"[^a-z0-9]+", " ", _s(programme).lower()).strip()
    words = set(normalised.split())
    if "pcp" in words or (
        "project" in words
        and bool({"control", "controls"} & words)
        and "professional" in words
    ):
        return PROGRAMME_ASSIGNMENT_TEMPLATES["pcp"]
    if "mm" in words or ("marketing" in words and "manager" in words):
        return PROGRAMME_ASSIGNMENT_TEMPLATES["mm"]
    if "me" in words or ("marketing" in words and "executive" in words):
        return PROGRAMME_ASSIGNMENT_TEMPLATES["me"]
    return None


def _component_assignment_template(component):
    """Return a workbook only when this Assignment belongs to ME/MM/PCP material."""
    module_id = _s(component.get("moduleId"))
    for code, module_ids in ASSIGNMENT_MODULE_TEMPLATE_IDS.items():
        if module_id in module_ids:
            return PROGRAMME_ASSIGNMENT_TEMPLATES[code]

    module_title = re.sub(
        r"[^a-z0-9]+", " ", _s(component.get("module")).lower()
    ).strip()
    if not module_title:
        return None

    pcp_markers = (
        "pmi sp", "schedule professional", "scheduling professional",
        "project planning", "project control", "project management office",
        "project management professional", "risk management", "earned value",
        "portfolio management", "managing successful programmes",
    )
    mm_markers = (
        "strategy planning", "customer journey", "commercial intelligence",
        "ai in marketing", "marketing manager",
    )
    me_markers = (
        "impact planning", "social media", "marketing technology", "martech",
        "marketing executive",
    )
    for code, markers in (("pcp", pcp_markers), ("mm", mm_markers), ("me", me_markers)):
        if any(marker in module_title for marker in markers):
            return PROGRAMME_ASSIGNMENT_TEMPLATES[code]
    return None


def _apply_programme_assignment_template(components, programme):
    """Fill blank ME/MM/PCP material assignments without changing other content."""
    for component in components:
        component_type = _s(component.get("type")).strip().lower().replace("-", "_")
        if component_type != "assignment" or _s(component.get("resourceUrl")):
            continue
        template = _component_assignment_template(component)
        if not template:
            continue
        component["resourceUrl"] = template["resourceUrl"]
        component["fileName"] = template["fileName"]
        component["downloadAllowed"] = True
    return components


def component_audio_url(settings, component_type):
    """The audio a component should offer, or None.

    ``uploadedFileUrl`` is the generic "file attached to this component" key: on
    a reading it is the PDF, on a slide deck the .pptx. Treating it as audio put
    an empty player under every reading with an attachment, pointed at a
    document — so it only counts for a podcast, or when the file itself is audio.
    """
    settings = settings if isinstance(settings, dict) else {}
    explicit = _s(settings.get("podcastUrl")) or _s(settings.get("audioUrl"))
    if explicit:
        return explicit
    uploaded = _s(settings.get("uploadedFileUrl"))
    if not uploaded:
        return None
    if component_type == "podcast":
        return uploaded
    suffix = Path(urlparse(uploaded).path).suffix.lower()
    return uploaded if suffix in AUDIO_FILE_SUFFIXES else None

# The enrolment record is the source: every learner exists in
# enrolment."Created_users" from the moment they are created, whereas the
# "Learner"."learners" profile only appears once enrolment is finished. Reading
# the source here is what lets a still-onboarding learner load their page at all
# (and carry a real programmeStatus, so the onboarding redirect can fire).
# `kind` remains in the URL for backwards-compatible frontend routes; ids are
# unique across the single table, so both resolve the same way.
SOURCE_MODELS = {
    "commercial": EnrolmentUser,
    "apprenticeship": EnrolmentUser,
}

IFRAME_SRC_RE = re.compile(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _active_profile_for_source(source, source_pk, *, heal=True):
    """Resolve the saved profile, including learners awaiting an invitation.

    ``Created_users`` and ``Learner.learners`` have independent primary-key
    sequences, so their ids are no longer guaranteed to match.  Email is the
    shared learner identity; the id lookup remains only as a compatibility
    fallback for older records that do not have an email.
    """
    # Saved progress/KSBs remain visible to authorised viewers before activation.
    profile = learner_profile_for_source(source, source_pk, **({'heal': False} if not heal else {}))
    if profile is None:
        return None

    # The detail serializer walks progress -> KSB links -> quiz answers and
    # their selected/correct answers.  Without prefetching, that becomes one
    # database round-trip per progress row/answer (and the OTJ calculation
    # reads the same graph again).  Load the complete graph in a fixed number
    # of queries so learner pages stay fast as their history grows.
    try:
        # ``Learner.learner_ksbs`` was the legacy per-learner snapshot and is
        # absent from current environments. Do not prefetch it unconditionally:
        # the LearnerProfile.ksbs compatibility property will query it lazily
        # only when an older profile has no current KSB assignment.
        prefetch_related_objects(
            [profile],
            "ksb_assignment__profile_version__definitions",
            "progress_entries__ksb_links",
            "progress_entries__quiz_answers__chosen_answers",
            "progress_entries__quiz_answers__correct_answers",
        )
    except AttributeError:
        logger.warning("Could not prefetch learner detail graph for profile %s.", source_pk, exc_info=True)
    return profile


def _normalise_weight_class(value, classification=""):
    raw = _s(value).lower()
    if raw in {"hard", "soft", "possible"}:
        return raw
    classification = _s(classification).lower()
    if classification == "main":
        return "hard"
    if classification == "possible":
        return "possible"
    return "soft"


def _component_ksb_items(value):
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else []
        except (TypeError, ValueError):
            value = []
    if not isinstance(value, list):
        return []
    items = []
    seen = set()
    for mapping in value:
        if not isinstance(mapping, dict):
            continue
        code = _s(mapping.get("code") or mapping.get("ksbCode") or mapping.get("ksb_code")).upper()
        if not code or code in seen:
            continue
        seen.add(code)
        classification = _s(mapping.get("classification") or mapping.get("type")) or None
        weight = mapping.get("weight")
        try:
            weight = float(weight or 0)
        except (TypeError, ValueError):
            weight = 0.0
        items.append({
            "code": code,
            "description": _s(mapping.get("description") or mapping.get("ksbDescription") or mapping.get("ksb_description")) or None,
            "classification": classification,
            "weight": weight,
            "weightClass": _normalise_weight_class(
                mapping.get("weightClass") if "weightClass" in mapping else mapping.get("weight_class"),
                classification,
            ),
        })
    return items


def _video_url_from_settings(settings):
    direct = _s(settings.get("videoUrl"))
    if direct:
        return direct
    iframe = _s(settings.get("embedCode"))
    match = IFRAME_SRC_RE.search(iframe)
    if match:
        return unescape(match.group(1))
    return None


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _humanise_type(type_):
    """Mirror frontend/src/api/curriculum.ts's humaniseType()."""
    if not type_:
        return ""
    spaced = type_.replace("_", " ")
    return spaced[:1].upper() + spaced[1:]


def _display_component_title(type_, title):
    """Mirror frontend/src/api/curriculum.ts's fetchComponents() title display
    logic — needed only for the legacy (pre-id) title-matching fallback below."""
    type_label = _humanise_type(type_)
    show_title = bool(title) and title.strip().lower() != type_label.lower()
    return f"{type_label} · {title}" if show_title else (type_label or title)


def _otjh_by_component_id(components):
    """Exact expected_otjh lookup for components saved with the structured
    plan format (they carry the real curriculum.components id)."""
    ids = sorted({c["componentId"] for c in components if c.get("componentId")})
    if not ids:
        return {}
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                "SELECT id, expected_otjh FROM curriculum.components WHERE id = ANY(%s)",
                [ids],
            )
            return {cid: (float(v) if v is not None else None) for cid, v in cur.fetchall()}
    except DatabaseError as exc:
        logger.warning("Could not look up expected_otjh by id: %s", exc)
        return {}


_WEEK_NUM_RE = re.compile(r"(?:week|wk)\s*-?\s*(\d+)", re.IGNORECASE)


def _week_number_from_title(title):
    """Best-effort week number from a free-text week label ('Week 1' -> 1)."""
    if not title:
        return None
    m = _WEEK_NUM_RE.search(title)
    return int(m.group(1)) if m else None


def _type_prefix_from_component_title(title):
    """The leading 'Type' label from a saved 'Type · Detail' component title.
    Falls back to the whole string when there's no separator."""
    if not title:
        return ""
    # Saved titles use the middle dot U+00B7 ('Live session · Live Teams session').
    head = title.split("·", 1)[0]
    return head.strip()


def _otjh_by_legacy_title(components):
    """Fallback expected_otjh lookup for components saved before the structured
    plan format existed (no real componentId to match on).

    Legacy plans carry client-generated week/component ids AND their own saved
    titles, both of which drift from the live curriculum once authors rename a
    week or edit a component title. So instead of relying on exact titles, we
    anchor on the parts that stay stable across those edits:
        module title  ->  week NUMBER  ->  component TYPE + ordinal position.
    Exact title still wins when it matches (unchanged plans); otherwise the
    Nth saved component of a given type maps to the Nth live component of that
    type in the same week (by display_order)."""
    legacy = [c for c in components if not c.get("componentId") and c.get("module")]
    if not legacy:
        return {}
    module_titles = sorted({c["module"] for c in legacy})
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                "SELECT module_catalogue_id, title FROM curriculum.modules "
                "WHERE title = ANY(%s)",
                [module_titles],
            )
            module_ids = {title: cat_id for cat_id, title in cur.fetchall()}
            if not module_ids:
                return {}

            cur.execute(
                "SELECT id, module_catalogue_id, title, week_number FROM curriculum.weeks "
                "WHERE module_catalogue_id = ANY(%s) "
                f"AND {visible_week_sql('weeks')} "
                "ORDER BY module_catalogue_id, week_number, display_order",
                [list(module_ids.values())],
            )
            weeks_by_title = {}   # (cat_id, title)       -> week_id
            weeks_by_number = {}  # (cat_id, week_number) -> week_id (first wins)
            for week_id, cat_id, title, week_number in cur.fetchall():
                weeks_by_title[(cat_id, title)] = week_id
                if week_number is not None:
                    weeks_by_number.setdefault((cat_id, int(week_number)), week_id)
            if not weeks_by_title:
                return {}

            cur.execute(
                "SELECT week_id, type, title, expected_otjh FROM curriculum.components "
                "WHERE week_id = ANY(%s) "
                "ORDER BY week_id, display_order",
                [list(set(weeks_by_title.values()))],
            )
            # Per week: exact display-title -> otjh, and ordered otjh-lists per humanised type.
            otjh_by_exact = {}                 # (week_id, display_title) -> otjh
            otjh_by_type = {}                  # (week_id, type_label)    -> [otjh, ...] in display_order
            for week_id, ctype, title, otjh in cur.fetchall():
                val = float(otjh) if otjh is not None else None
                otjh_by_exact[(week_id, _display_component_title(ctype, title))] = val
                otjh_by_type.setdefault((week_id, _humanise_type(ctype)), []).append(val)
    except DatabaseError as exc:
        logger.warning("Could not look up legacy expected_otjh: %s", exc)
        return {}

    result = {}
    type_cursor = {}  # (week_id, type_label) -> next ordinal to consume
    for c in legacy:
        mod_id = module_ids.get(c["module"])
        week_title = c.get("week")
        # Resolve the live week: exact title first, then by parsed week number.
        week_id = weeks_by_title.get((mod_id, week_title)) if mod_id else None
        if week_id is None and mod_id:
            num = _week_number_from_title(week_title)
            if num is not None:
                week_id = weeks_by_number.get((mod_id, num))

        otjh = None
        if week_id is not None:
            comp_title = c.get("component")
            otjh = otjh_by_exact.get((week_id, comp_title))
            if otjh is None:
                # Title drifted — map by component type + ordinal within the week.
                type_label = _type_prefix_from_component_title(comp_title)
                key = (week_id, type_label)
                idx = type_cursor.get(key, 0)
                candidates = otjh_by_type.get(key, [])
                if idx < len(candidates):
                    otjh = candidates[idx]
                type_cursor[key] = idx + 1

        result[(c.get("module"), c.get("week"), c.get("component"))] = otjh
    return result


def _display_quiz_title(title):
    """Same 'Type · Detail' shape as other components, keyed off the Quiz type."""
    label = "Quiz"
    show_title = bool(title) and title.strip().lower() != label.lower()
    return f"{label} · {title}" if show_title else label


def _resolve_week_ids(weeks):
    """Resolve missing IDs without merging same-titled module deliveries.

    Native identities remain authoritative. A title-only legacy row resolves
    only when both its module and its week are unambiguous.
    """
    candidates = {}

    def add(key, week_id):
        if week_id:
            candidates.setdefault(key, set()).add(week_id)

    for week in weeks:
        if week.get("weekId"):
            add((week.get("module"), week.get("week")), week["weekId"])
            if week.get("moduleId"):
                add(("moduleId", _s(week["moduleId"]), week.get("week")), week["weekId"])
    legacy = [w for w in weeks if not w.get("weekId") and w.get("module") and w.get("week")]
    if not legacy:
        return {key: next(iter(ids)) for key, ids in candidates.items() if len(ids) == 1}

    module_titles = sorted({w["module"] for w in legacy})
    explicit_module_ids = sorted({_s(w["moduleId"]) for w in legacy if w.get("moduleId")})
    ambiguous_keys = set()
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                "SELECT module_catalogue_id, title FROM curriculum.modules "
                "WHERE title = ANY(%s) OR module_catalogue_id = ANY(%s)",
                [module_titles, explicit_module_ids],
            )
            modules_by_title = {}
            known_module_ids = set()
            for module_id, title in cur.fetchall():
                known_module_ids.add(_s(module_id))
                modules_by_title.setdefault(title, set()).add(_s(module_id))
            scope_ids = set(explicit_module_ids) & known_module_ids
            scope_ids.update(next(iter(ids)) for ids in modules_by_title.values() if len(ids) == 1)
            if scope_ids:
                cur.execute(
                    "SELECT id, module_catalogue_id, title FROM curriculum.weeks "
                    "WHERE module_catalogue_id = ANY(%s) "
                    f"AND {visible_week_sql('weeks')}",
                    [sorted(scope_ids)],
                )
                weeks_by_module = {}
                for week_id, module_id, title in cur.fetchall():
                    weeks_by_module.setdefault((_s(module_id), title), set()).add(week_id)
            else:
                weeks_by_module = {}
    except DatabaseError as exc:
        logger.warning("Could not resolve legacy week ids: %s", exc)
        # A native row still uses its own explicit ID. Missing-ID legacy rows
        # must not borrow a candidate whose title uniqueness was not checked.
        legacy_keys = {("moduleId", _s(w["moduleId"]), w["week"]) if w.get("moduleId")
                       else (w["module"], w["week"]) for w in legacy}
        return {key: next(iter(ids)) for key, ids in candidates.items()
                if len(ids) == 1 and key not in legacy_keys}

    for week in legacy:
        module_id = _s(week.get("moduleId"))
        key = ("moduleId", module_id, week["week"]) if module_id else (week["module"], week["week"])
        ids = ({module_id} & known_module_ids) if module_id else modules_by_title.get(week["module"], set())
        if len(ids) != 1:
            ambiguous_keys.add(key)
            continue
        resolved_ids = weeks_by_module.get((next(iter(ids)), week["week"]), set())
        if len(resolved_ids) != 1:
            ambiguous_keys.add(key)
            continue
        add(key, next(iter(resolved_ids)))
    return {key: next(iter(ids)) for key, ids in candidates.items()
            if len(ids) == 1 and key not in ambiguous_keys}


_MODULE_ASSESSMENTS_WEEK_LABEL = "Module assessments"


def _module_assessment_week_id(module_id):
    return f"quiz-module-assessments::{module_id or 'legacy'}"


def _normalise_quiz_scope_value(value):
    return _s(value).strip().lower()


def _matching_module_ids_for_quiz_record(quiz_record, modules_by_id, explicit_module_ids):
    scoped_explicit = [module_id for module_id in explicit_module_ids if module_id in modules_by_id]
    if scoped_explicit:
        return scoped_explicit

    quiz_module = _normalise_quiz_scope_value(quiz_record.get("module"))
    quiz_programme_id = _normalise_quiz_scope_value(quiz_record.get("programme_id"))
    quiz_programme = _normalise_quiz_scope_value(quiz_record.get("programme"))
    if not quiz_module:
        return []

    exact_matches = []
    fuzzy_matches = []
    for module_id, meta in modules_by_id.items():
        module_name = _normalise_quiz_scope_value(meta.get("module"))
        if not module_name:
            continue
        module_matches_exact = module_name == quiz_module
        module_matches_fuzzy = module_matches_exact or quiz_module in module_name or module_name in quiz_module
        if not module_matches_fuzzy:
            continue

        programme_matches = False
        if quiz_programme_id and _normalise_quiz_scope_value(meta.get("programmeId")) == quiz_programme_id:
            programme_matches = True
        elif quiz_programme and (
            _normalise_quiz_scope_value(meta.get("programme")) == quiz_programme
            or _normalise_quiz_scope_value(meta.get("programmeId")) == quiz_programme
        ):
            programme_matches = True
        elif not quiz_programme_id and not quiz_programme:
            programme_matches = True

        if not programme_matches:
            continue
        if module_matches_exact:
            exact_matches.append(module_id)
        else:
            fuzzy_matches.append(module_id)

    if exact_matches:
        return exact_matches
    if fuzzy_matches:
        if quiz_programme_id or quiz_programme:
            return fuzzy_matches
        programme_keys = {
            (
                _normalise_quiz_scope_value(modules_by_id[module_id].get("programmeId")),
                _normalise_quiz_scope_value(modules_by_id[module_id].get("programme")),
            )
            for module_id in fuzzy_matches
        }
        if len(programme_keys) == 1:
            return fuzzy_matches
    return []


def _append_week_quizzes(weeks, components, assigned_modules=None, *, strict=False, native_week_ids=False, resolved_metadata=None):
    """Append published quizzes for the learner's weeks/modules.

    Visibility now honours explicit module assignments (curriculum.quiz_course_links)
    and falls back to the quiz's own programme/module metadata when older records
    were never backfilled into the link table. When no valid real week can be
    resolved, quizzes land in a synthetic "Module assessments" bucket inside the
    assigned module instead of silently disappearing.
    """
    week_ids_by_key = _resolve_week_ids(weeks)

    def resolved_week_id(week):
        # Never replace an explicit native ID with a title-keyed fallback.
        # Keep native_week_ids in the call signature for older callers.
        if week.get("weekId"):
            return _s(week["weekId"])
        module_id = _s(week.get("moduleId"))
        key = ("moduleId", module_id, week.get("week")) if module_id else (week.get("module"), week.get("week"))
        return _s(week_ids_by_key.get(key))

    resolved_week_ids = sorted({resolved_week_id(w) for w in weeks if resolved_week_id(w)})
    week_scopes_by_id = {}
    for week in weeks:
        week_id = resolved_week_id(week)
        module_id = _s(week.get("moduleId")) or _s(week.get("module"))
        if week_id and module_id:
            week_scopes_by_id.setdefault(week_id, set()).add(module_id)
    weeks_by_module_id = {}
    module_order = list(dict.fromkeys(
        _s(entry.get("moduleId")) for entry in (assigned_modules or [])
        if isinstance(entry, dict) and _s(entry.get("moduleId"))
    ))
    module_titles_by_id = {}
    for week in weeks:
        module_id = _s(week.get("moduleId"))
        if not module_id:
            continue
        if module_id not in module_order:
            module_order.append(module_id)
        module_titles_by_id.setdefault(module_id, _s(week.get("module")))
        weeks_by_module_id.setdefault(module_id, []).append({
            "weekId": resolved_week_id(week),
            "week": _s(week.get("week")),
            "module": _s(week.get("module")),
        })

    if not resolved_week_ids and not module_order:
        return weeks, components

    modules_by_id = {}
    explicit_module_ids_by_quiz = {}
    explicit_week_ids_by_quiz_module = {}
    quiz_rows_by_id = {}
    authored_quizzes = set()

    def store_quiz_rows(rows):
        for row in rows:
            quiz_rows_by_id[row[0]] = {
                "id": row[0],
                "weekId": _s(row[1]),
                "title": _s(row[2]),
                "questions": row[3],
                "duration": row[4],
                "timeUnit": row[5],
                "programme_id": _s(row[6]),
                "programme": _s(row[7]),
                "module": _s(row[8]),
            }

    try:
        with connections["enrolment"].cursor() as cur:
            cached_modules = (resolved_metadata or {}).get('quiz_modules')
            if module_order and cached_modules is not None:
                modules_by_id = {mid:{key:_s(value) for key,value in meta.items()}
                    for mid,meta in cached_modules.items() if mid in module_order}
                module_order = [mid for mid in module_order if mid in modules_by_id]
            elif module_order:
                cur.execute(
                    """
                    SELECT m.module_catalogue_id, m.title, COALESCE(m.programme_id, ''), COALESCE(m.programme_name, '')
                    FROM curriculum.modules m
                    WHERE m.module_catalogue_id = ANY(%s)
                      AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent, '') <> '')
                    """,
                    [module_order],
                )
                for module_id, module_title, programme_id, programme_name in cur.fetchall():
                    module_id = _s(module_id)
                    if not module_id:
                        continue
                    modules_by_id[module_id] = {
                        "module": _s(module_title) or module_titles_by_id.get(module_id) or module_id,
                        "programmeId": _s(programme_id),
                        "programme": _s(programme_name),
                    }
                module_order = [module_id for module_id in module_order if module_id in modules_by_id]
            if resolved_week_ids:
                cur.execute(
                    """
                    SELECT id, week_id, title, questions, duration, time_unit,
                           COALESCE(programme_id, ''), COALESCE(programme, ''), COALESCE(module, '')
                    FROM curriculum.quizzes
                    WHERE lower(COALESCE(status, '')) = 'published'
                      AND week_id = ANY(%s)
                    """,
                    [resolved_week_ids],
                )
                store_quiz_rows(cur.fetchall())
            if module_order:
                try:
                    cur.execute(
                        """
                        SELECT quiz_id, module_catalogue_id, COALESCE(week_id, '')
                        FROM curriculum.quiz_course_links
                        WHERE module_catalogue_id = ANY(%s)
                        """,
                        [module_order],
                    )
                    for quiz_id, module_id, week_id in cur.fetchall():
                        module_id = _s(module_id)
                        if module_id:
                            explicit_module_ids_by_quiz.setdefault(quiz_id, set()).add(module_id)
                            explicit_week_ids_by_quiz_module.setdefault(quiz_id, {})[module_id] = _s(week_id)
                except DatabaseError:
                    if strict:
                        raise
                    explicit_module_ids_by_quiz = {}
                    explicit_week_ids_by_quiz_module = {}

                explicit_quiz_ids = sorted(explicit_module_ids_by_quiz)
                if explicit_quiz_ids:
                    cur.execute(
                        """
                        SELECT id, week_id, title, questions, duration, time_unit,
                               COALESCE(programme_id, ''), COALESCE(programme, ''), COALESCE(module, '')
                        FROM curriculum.quizzes
                        WHERE lower(COALESCE(status, '')) = 'published'
                          AND id = ANY(%s)
                        """,
                        [explicit_quiz_ids],
                    )
                    store_quiz_rows(cur.fetchall())

                module_titles = sorted({
                    _normalise_quiz_scope_value(meta.get("module"))
                    for meta in modules_by_id.values()
                    if _normalise_quiz_scope_value(meta.get("module"))
                })
                if module_titles:
                    cur.execute(
                        """
                        SELECT id, week_id, title, questions, duration, time_unit,
                               COALESCE(programme_id, ''), COALESCE(programme, ''), COALESCE(module, '')
                        FROM curriculum.quizzes
                        WHERE lower(COALESCE(status, '')) = 'published'
                          AND lower(COALESCE(module, '')) = ANY(%s)
                        """,
                        [module_titles],
                    )
                    store_quiz_rows(cur.fetchall())
            represented = {
                (_s(c.get('moduleId')), str(c['quizMeta']['quizId']))
                for c in components if c.get('isQuiz') and isinstance(c.get('quizMeta'), dict)
                and c['quizMeta'].get('quizId') is not None
            }
            pending_modules, pending_quizzes = set(), set()
            for quiz in quiz_rows_by_id.values():
                targets = _matching_module_ids_for_quiz_record(
                    quiz, modules_by_id, explicit_module_ids_by_quiz.get(quiz['id'], set()))
                if not targets and quiz['weekId'] in resolved_week_ids:
                    targets = [mid for mid, items in weeks_by_module_id.items()
                               if any(item['weekId'] == quiz['weekId'] for item in items)]
                for mid in targets:
                    if (mid, str(quiz['id'])) not in represented:
                        pending_modules.add(mid)
                        pending_quizzes.add(quiz['id'])
            if pending_modules and pending_quizzes:
                # A quiz that belongs (or belonged) to a builder component is
                # delivered only through the current authored component tree.
                # Deleted/relinked slots must not reappear through stale course
                # links or quiz metadata. Keep history for progress, and scope
                # ownership per module so independent assignments still work.
                # Already represented quizzes need no history scan.
                scope_ids = sorted(pending_modules)
                quiz_ids = sorted(pending_quizzes)
                quiz_refs = [str(quiz_id) for quiz_id in quiz_ids]
                cur.execute(
                    """
                    SELECT c.module_catalogue_id, c.settings_json->>'linkedQuizId'
                    FROM curriculum.components c
                    WHERE c.module_catalogue_id = ANY(%s)
                      AND c.settings_json->>'linkedQuizId' = ANY(%s)
                    UNION
                    SELECT c.module_catalogue_id, link.quiz_id::text
                    FROM curriculum.quiz_component_links link
                    JOIN curriculum.components c ON c.id = link.component_id
                    WHERE c.module_catalogue_id = ANY(%s) AND link.quiz_id = ANY(%s)
                    UNION
                    SELECT r.module_catalogue_id, r.snapshot->'settings_json'->>'linkedQuizId'
                    FROM curriculum.record_revisions r
                    WHERE r.entity_type = 'component' AND r.module_catalogue_id = ANY(%s)
                      AND r.snapshot->'settings_json'->>'linkedQuizId' = ANY(%s)
                    """,
                    [scope_ids, quiz_refs, scope_ids, quiz_ids, scope_ids, quiz_refs],
                )
                authored_quizzes = {(_s(module_id), str(quiz_id)) for module_id, quiz_id in cur.fetchall()}
    except DatabaseError as exc:
        logger.warning("Could not look up learner-visible quizzes: %s", exc)
        if strict:
            raise
        return weeks, components

    if not quiz_rows_by_id:
        return weeks, components

    week_quizzes_by_scope = {}
    module_level_quizzes_by_module_id = {}
    for quiz in quiz_rows_by_id.values():
        explicit_module_ids = explicit_module_ids_by_quiz.get(quiz["id"], set())
        explicit_week_ids_by_module = explicit_week_ids_by_quiz_module.get(quiz["id"], {})
        matched_module_ids = _matching_module_ids_for_quiz_record(quiz, modules_by_id, explicit_module_ids)
        placed_module_ids = set()
        for module_id in matched_module_ids:
            module_week_ids = {
                item["weekId"]
                for item in weeks_by_module_id.get(module_id, [])
                if item.get("weekId")
            }
            explicit_week_id = _s(explicit_week_ids_by_module.get(module_id))
            target_week_id = ""
            if explicit_week_id and explicit_week_id in module_week_ids:
                target_week_id = explicit_week_id
            elif quiz["weekId"] and quiz["weekId"] in module_week_ids:
                target_week_id = quiz["weekId"]
            if target_week_id:
                week_quizzes_by_scope.setdefault((module_id, target_week_id), []).append(quiz)
                placed_module_ids.add(module_id)
        if not matched_module_ids and quiz["weekId"] and quiz["weekId"] in resolved_week_ids:
            owners = week_scopes_by_id.get(quiz["weekId"], set())
            if len(owners) == 1:
                week_quizzes_by_scope.setdefault((next(iter(owners)), quiz["weekId"]), []).append(quiz)
            continue
        for module_id in matched_module_ids:
            if module_id in placed_module_ids:
                continue
            module_level_quizzes_by_module_id.setdefault(module_id, []).append(quiz)

    if not week_quizzes_by_scope and not module_level_quizzes_by_module_id:
        return weeks, components

    next_weeks = list(weeks)
    next_components = list(components)
    # Builder placement is authoritative. A published quiz may still have an
    # old/missing week_id; that must not append a second copy of a quiz already
    # linked to a component in this module. Other modules remain independent.
    seen_quizzes = set(authored_quizzes)
    for component in next_components:
        quiz_meta = component.get("quizMeta")
        if not component.get("isQuiz") or not isinstance(quiz_meta, dict):
            continue
        quiz_id = quiz_meta.get("quizId")
        if quiz_id is None:
            continue
        seen_quizzes.add((
            _s(component.get("moduleId")) or _s(component.get("module")),
            str(quiz_id),
        ))

    for week in weeks:
        module_id = _s(week.get("moduleId")) or _s(week.get("module"))
        week_id = resolved_week_id(week)
        for quiz in week_quizzes_by_scope.get((module_id, week_id), []):
            key = (module_id, str(quiz["id"]))
            if key in seen_quizzes:
                continue
            next_components.append({
                "module": week.get("module"),
                "week": week.get("week"),
                "component": _display_quiz_title(quiz["title"]),
                "moduleId": week.get("moduleId"),
                "weekId": week_id,
                "componentId": None,
                "expectedOtjh": None,
                "isQuiz": True,
                "quizMeta": {
                    "quizId": quiz["id"],
                    "questions": quiz["questions"],
                    "duration": quiz["duration"],
                    "timeUnit": quiz["timeUnit"],
                },
            })
            seen_quizzes.add(key)

    existing_week_ids = {_s(week.get("weekId")) for week in next_weeks if _s(week.get("weekId"))}
    for module_id in module_order:
        queued = [quiz for quiz in module_level_quizzes_by_module_id.get(module_id, [])
                  if (module_id, str(quiz["id"])) not in seen_quizzes]
        if not queued:
            continue
        module_title = modules_by_id.get(module_id, {}).get("module") or module_titles_by_id.get(module_id) or module_id
        synthetic_week_id = _module_assessment_week_id(module_id)
        if synthetic_week_id not in existing_week_ids:
            next_weeks.append({
                "module": module_title,
                "week": _MODULE_ASSESSMENTS_WEEK_LABEL,
                "moduleId": module_id,
                "weekId": synthetic_week_id,
            })
            existing_week_ids.add(synthetic_week_id)
        for quiz in queued:
            key = (module_id, str(quiz["id"]))
            if key in seen_quizzes:
                continue
            next_components.append({
                "module": module_title,
                "week": _MODULE_ASSESSMENTS_WEEK_LABEL,
                "component": _display_quiz_title(quiz["title"]),
                "moduleId": module_id,
                "weekId": synthetic_week_id,
                "componentId": None,
                "expectedOtjh": None,
                "isQuiz": True,
                "quizMeta": {
                    "quizId": quiz["id"],
                    "questions": quiz["questions"],
                    "duration": quiz["duration"],
                    "timeUnit": quiz["timeUnit"],
                },
            })
            seen_quizzes.add(key)

    return next_weeks, next_components


def otjh_status_from_variance(variance):
    """RAG status from the OTJH hour difference (actual minus target).

    The coach and learner surfaces use the same absolute-hour thresholds:
    a shortfall of 20 hours needs attention and a shortfall of 40 hours is at
    risk. ``variance`` is retained as the argument name for compatibility with
    existing callers, but it is an hour difference rather than a percentage.
    """
    if variance is None:
        return "On track"
    shortfall = -Decimal(str(variance))
    if shortfall > Decimal("40"):
        return "At risk"
    if shortfall > Decimal("20"):
        return "Need attention"
    return "On track"


def _otjh_status(variance):
    """Backward-compatible wrapper for the shared OTJH RAG rule."""
    return otjh_status_from_variance(variance)


def _week_target_rows(detail):
    weeks = detail.get("week") or []
    if not weeks:
        return []

    totals_by_week = {}
    for component in detail.get("components", []):
        # A quiz has a duration/time limit for the learner, but that duration is
        # actual activity time once an attempt is submitted; it is not authored
        # planned OTJH. Keep it out of week targets while preserving the quiz's
        # own expectedOtjh/quizMeta values for delivery and reporting.
        if component.get("isQuiz") or _s(component.get("type")).strip().lower() == "quiz":
            continue
        key = (
            component.get("module"),
            component.get("week"),
            component.get("moduleId"),
            component.get("weekId"),
        )
        totals_by_week[key] = round(totals_by_week.get(key, 0.0) + float(component.get("expectedOtjh") or 0.0), 2)

    return [
        {
            "module": week.get("module"),
            "week": week.get("week"),
            "moduleId": week.get("moduleId"),
            "weekId": week.get("weekId"),
            "otjh": totals_by_week.get(
                (
                    week.get("module"),
                    week.get("week"),
                    week.get("moduleId"),
                    week.get("weekId"),
                ),
                0.0,
            ),
        }
        for week in weeks
    ]


def _week_component_count_rows(detail):
    """Same shape as _week_target_rows, but counting components per week
    instead of summing their expected_otjh."""
    weeks = detail.get("week") or []
    if not weeks:
        return []

    counts_by_week = {}
    for component in detail.get("components", []):
        key = (
            component.get("module"),
            component.get("week"),
            component.get("moduleId"),
            component.get("weekId"),
        )
        counts_by_week[key] = counts_by_week.get(key, 0) + 1

    return [
        {
            "module": week.get("module"),
            "week": week.get("week"),
            "moduleId": week.get("moduleId"),
            "weekId": week.get("weekId"),
            "components": counts_by_week.get(
                (
                    week.get("module"),
                    week.get("week"),
                    week.get("moduleId"),
                    week.get("weekId"),
                ),
                0,
            ),
        }
        for week in weeks
    ]


def _sequential_week_target(week_rows, learner_start_date=None, today=None):
    if not week_rows:
        return 0.0
    if learner_start_date is None:
        return round(float(week_rows[0].get("otjh") or 0.0), 2)
    today = today or timezone.localdate()
    weeks_elapsed = max(0, (today - learner_start_date).days // 7)
    return round(sum(float(row.get("otjh") or 0.0) for row in week_rows[: weeks_elapsed + 1]), 2)


def _schedule_based_week_target(week_rows, module_start_by_id, week_offset_by_id, today=None):
    if not week_rows:
        return None
    today = today or timezone.localdate()
    total = 0.0
    has_schedule = False
    for row in week_rows:
        module_id = row.get("moduleId")
        week_id = row.get("weekId")
        start_date = module_start_by_id.get(module_id)
        offset = week_offset_by_id.get(week_id)
        if start_date is None or offset is None:
            continue
        has_schedule = True
        week_start = start_date + timedelta(days=offset * 7)
        if week_start <= today:
            total += float(row.get("otjh") or 0.0)
    return round(total, 2) if has_schedule else None


def _cumulative_week_target(detail, learner_start_date=None, today=None):
    """Planned hours the learner should have reached by today.

    Primary path: use each module's authored start_date plus the week's
    display_order from curriculum.weeks, summing every week whose scheduled
    start is on or before today.

    Fallback: when schedule metadata is incomplete, use the learner's own
    start_date and the saved week order as a sequential week-by-week pace.
    """
    week_rows = _week_target_rows(detail)
    if not week_rows:
        return 0.0

    module_ids = sorted({row.get("moduleId") for row in week_rows if row.get("moduleId")})
    week_ids = sorted({row.get("weekId") for row in week_rows if row.get("weekId")})
    module_start_by_id = {}
    week_offset_by_id = {}

    if module_ids and week_ids:
        try:
            with connections["enrolment"].cursor() as cur:
                cur.execute(
                    "SELECT module_catalogue_id, start_date FROM curriculum.modules "
                    "WHERE module_catalogue_id = ANY(%s)",
                    [module_ids],
                )
                module_start_by_id = {module_id: start_date for module_id, start_date in cur.fetchall() if start_date}

                cur.execute(
                    "SELECT id, display_order, week_number FROM curriculum.weeks "
                    "WHERE id = ANY(%s)",
                    [week_ids],
                )
                for week_id, display_order, week_number in cur.fetchall():
                    if display_order is not None:
                        week_offset_by_id[week_id] = max(int(display_order), 0)
                    elif week_number is not None:
                        week_offset_by_id[week_id] = max(int(week_number) - 1, 0)
        except DatabaseError as exc:
            logger.warning("Could not resolve week schedule metadata for OTJ target: %s", exc)

    scheduled_target = _schedule_based_week_target(
        week_rows,
        module_start_by_id,
        week_offset_by_id,
        today=today,
    )
    if scheduled_target is not None:
        return scheduled_target

    total = _sequential_week_target(week_rows, learner_start_date=learner_start_date, today=today)
    return round(total, 2)


def _review_otjh_hours(learner_profile):
    """(planned, completed) OTJ hours this learner's Reviews are worth.

    Curriculum owns both numbers -- see curriculum_api.review_otjh. Planned
    counts every occurrence the Review Engine projects across the learner's
    whole programme; completed counts only instances actually signed off. Only
    templates flagged counts_towards_otjh contribute to either.
    """
    if learner_profile is None:
        return 0.0, 0.0

    from curriculum_api import review_otjh

    learner_id = getattr(learner_profile, "id", None)
    start_date = getattr(learner_profile, "start_date", None)
    # The forecast spans the learner's whole programme, not a month: this
    # feeds the same "planned hours" figure totalExpectedOtjh does, which is
    # the plan's lifetime total. Without an end date there is no window to
    # project into, so reviews contribute nothing rather than an arbitrary
    # horizon that would silently inflate the total.
    end_date = getattr(learner_profile, "end_date", None)
    from coach_api.review_sources import resolve_review_source
    if resolve_review_source(profile_aptem_id=getattr(learner_profile, "aptem_id", None)).kind == "aptem":
        from coach_api.migrated_otjh import planned_meetings
        planned = sum(row["hours"] for row in planned_meetings(learner_profile, start_date, end_date)) if start_date and end_date else 0
        # Newly credited migrated reviews already exist in training_plan_progress
        # and the canonical ledger. Only old Native credit is additional here.
        return planned, review_otjh.completed_hours(learner_id)
    if not learner_id or not start_date or not end_date:
        return 0.0, review_otjh.completed_hours(learner_id) if learner_id else 0.0

    planned = review_otjh.planned_hours(
        _s(getattr(learner_profile, "programme_id", "")),
        learner_id,
        _s(getattr(learner_profile, "programme_status", "")),
        start_date,
        start_date,
        end_date,
    )
    return planned, review_otjh.completed_hours(learner_id)


def _live_otjh_snapshot(detail, learner_profile=None):
    # Reviews are off-the-job training in their own right when Curriculum says
    # so, so they land in the same two totals as curriculum components rather
    # than a figure of their own: planned grows when a review is scheduled,
    # completed when it is signed off.
    review_planned, review_completed = _review_otjh_hours(learner_profile)
    planned = fmt_hours(float(detail.get("totalExpectedOtjh") or 0) + review_planned)
    completed = (
        completed_hours_from_progress(
            learner_profile.training_plan_progress,
            detail.get("components"),
        )
        if learner_profile
        else "0"
    )
    if review_completed:
        completed = fmt_hours(float(completed or 0) + review_completed)

    learner_start_date = getattr(learner_profile, "start_date", None)
    target_num = _cumulative_week_target(detail, learner_start_date=learner_start_date)
    # Same elapsed-weeks approximation as the OTJH target's own sequential
    # fallback -- reused here (active_users.week_by_elapsed_time) so the coach
    # caseload card/table and this page never disagree on what "current week"
    # means for the same learner.
    flat_weeks = [
        (row.get("module"), row.get("week"))
        for row in _week_target_rows(detail)
        if row.get("week")
    ]
    current_week = week_by_elapsed_time(flat_weeks, learner_start_date)
    # Same elapsed-weeks pacing, applied to component counts instead of hours,
    # so "Components: 4/7" reads the way "OTJH: 12/20 hrs" does -- against
    # what's expected by now, not the whole plan.
    component_counts = [row.get("components", 0) for row in _week_component_count_rows(detail)]
    components_target = target_by_elapsed_time(component_counts, learner_start_date)
    completed_num = float(completed) if completed else 0.0
    progress_hours_num = round(completed_num - target_num, 2)
    variance = round((completed_num - target_num) / target_num, 2) if target_num else None

    target_str = fmt_hours(target_num)
    progress_hours_str = fmt_hours(progress_hours_num) if progress_hours_num >= 0 else f"-{fmt_hours(abs(progress_hours_num))}"
    variance_str = "" if variance is None else str(variance)
    variance_db = None if variance is None else variance
    otjh_status = _otjh_status(progress_hours_num)

    return {
        "planned_hours": planned,
        "completed_hours": completed,
        "target_hours": target_str,
        "progress_hours": progress_hours_str,
        "progress_variance": variance_db,
        "otjh_status": otjh_status,
        "plannedHours": planned,
        "completedHours": completed,
        "targetHours": target_str,
        "progressHours": progress_hours_str,
        "progressVariance": variance_str,
        "otjhStatus": otjh_status,
        "currentModule": current_week[0] if current_week else None,
        "currentWeek": current_week[1] if current_week else None,
        "componentsTargetToDate": components_target,
    }


def _apply_live_otjh_snapshot(detail, snapshot):
    detail["plannedHours"] = snapshot["plannedHours"]
    detail["completedHours"] = snapshot["completedHours"]
    detail["targetHours"] = snapshot["targetHours"]
    detail["progressHours"] = snapshot["progressHours"]
    detail["progressVariance"] = snapshot["progressVariance"]
    detail["otjhStatus"] = snapshot["otjhStatus"]
    detail["currentModule"] = snapshot["currentModule"]
    detail["currentWeek"] = snapshot["currentWeek"]
    detail["componentsTargetToDate"] = snapshot["componentsTargetToDate"]
    return detail


def _normalized_snapshot_value(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def persist_live_otjh_snapshot(learner_profile, snapshot):
    if learner_profile is None:
        return []

    calculated = {
        "planned_hours": snapshot["planned_hours"],
        "completed_hours": snapshot["completed_hours"],
        "target_hours": snapshot["target_hours"],
        "progress_hours": snapshot["progress_hours"],
        "progress_variance": snapshot["progress_variance"],
        "otjh_status": snapshot["otjh_status"],
    }
    changed_fields = []
    for field, value in calculated.items():
        if _normalized_snapshot_value(getattr(learner_profile, field, None)) != _normalized_snapshot_value(value):
            setattr(learner_profile, field, value)
            changed_fields.append(field)
    if changed_fields:
        learner_profile.save(update_fields=changed_fields)
    return changed_fields


def build_otjh_detail(source, learner_profile=None):
    """One learner's own resolved training plan -- modules/weeks/components with
    expectedOtjh attached and totalExpectedOtjh summed.

    Extracted from refresh_learner_otjh_snapshot (its only original caller, and
    still its default path) so a second consumer that needs the same resolved
    plan -- the Progress Review snapshot, see review_progress_snapshot.py --
    reads it through one builder instead of repeating this resolution chain.
    """
    detail = to_learner_detail(source, learner_profile)
    resolved_plan = effective_training_plan(source)
    detail["modules"], detail["week"], detail["components"] = _resolve_from_master(
        detail["modules"], detail["week"], detail["components"],
        assigned_modules=resolved_plan,
    )
    detail["components"] = _apply_programme_assignment_template(
        detail["components"], getattr(source, "programme", "")
    )
    detail["components"], detail["totalExpectedOtjh"] = _annotate_otjh(detail["components"])
    detail["week"], detail["components"] = _append_week_quizzes(
        detail["week"],
        detail["components"],
        assigned_modules=resolved_plan,
    )
    return detail


def refresh_learner_otjh_snapshot(learner_profile, *, source=None, detail=None):
    if learner_profile is None:
        return {}

    resolved_source = source or learner_profile
    resolved_detail = detail
    if resolved_detail is None:
        resolved_detail = build_otjh_detail(resolved_source, learner_profile)

    snapshot = _live_otjh_snapshot(resolved_detail, learner_profile)
    snapshot["components_planned"] = len(resolved_detail.get("components") or [])
    snapshot["componentsPlanned"] = snapshot["components_planned"]
    _apply_live_otjh_snapshot(resolved_detail, snapshot)
    persist_live_otjh_snapshot(learner_profile, snapshot)
    return snapshot


def _authored_source_type(settings):
    """Which source an author chose for a component: a link, or an upload.

    The builder writes it as `powerpointSource` (or `uploadSource`), and rows
    that came through the older authoring path carry it inside `legacySettings`,
    which is itself a JSON string. Both are read, because both exist in the data.
    """
    for key in ("powerpointSource", "uploadSource"):
        value = _s(settings.get(key))
        if value:
            return value
    legacy = settings.get("legacySettings")
    if isinstance(legacy, str):
        try:
            legacy = json.loads(legacy)
        except ValueError:
            legacy = None
    if isinstance(legacy, dict):
        for key in ("powerpointSource", "uploadSource"):
            value = _s(legacy.get(key))
            if value:
                return value
    return ""


# Every way a component can name its attached document, in the order they are
# tried when nothing says which the author meant.
# Does this authored text actually carry markup, or is it plain prose that
# happens to sit in a rich-text field? Used to decide whether a brief is safe to
# render as HTML — see the assignment brief resolution below.
_HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")

# Every component is seeded with a boilerplate reflection prompt (see
# BASE_COMPONENT_SETTINGS in curriculum_api.views and defaultReflectionPrompt in
# the Module Builder), so an untouched one carries no authored instruction — it
# is the same sentence on every activity in the programme. Showing it as "what
# to do" tells the learner nothing and crowds out the real task, so a prompt
# still matching its default is treated as unset.
_DEFAULT_REFLECTION_PROMPTS = frozenset({
    "what did you learn? how will you apply this at work? which ksbs did this develop?",
    "which questions or topics do you need to revisit after this activity?",
})


def _authored_reflection_prompt(prompt):
    """The reflection prompt only if a human actually wrote one."""
    if not prompt:
        return None
    return None if prompt.strip().casefold() in _DEFAULT_REFLECTION_PROMPTS else prompt

_RESOURCE_URL_KEYS = (
    "resourceUrl",
    "presentationUrl",
    "externalUrl",
    "fileUrl",
    "uploadedFileUrl",
    "url",
)
# The same keys with the file we host first, for a component whose author chose
# to upload one.
_UPLOADED_FIRST_KEYS = (
    "uploadedFileUrl",
    "fileUrl",
    "resourceUrl",
    "presentationUrl",
    "externalUrl",
    "url",
)


def _component_resource_url(settings):
    """The document a learner should be shown for this component.

    A component can hold both a link and an uploaded file: switching source type
    in the builder leaves the other field exactly as it was, so a deck uploaded
    after a link was tried keeps that link on the record. The author's chosen
    source is therefore what decides which one is served — reading the link first
    regardless showed the learner a document they have no access to (a personal
    SharePoint share, say) while the real deck sat uploaded beside it.

    With no source recorded — every bulk-imported row — the original order
    stands; those carry the same hosted path in both fields anyway.
    """
    uploaded = "upload" in _authored_source_type(settings).casefold()
    keys = _UPLOADED_FIRST_KEYS if uploaded else _RESOURCE_URL_KEYS
    for key in keys:
        value = _s(settings.get(key))
        if value:
            return value
    return None


_COMPONENT_FILE_URL_PREFIXES = ("https://", "http://", "/curriculum_api/curriculum/uploads/")


def _component_files(settings, resource_url, file_name):
    """Every file attached to this component, in the order the author set.

    ``componentFiles`` is the ordered list the Module Builder writes once a
    component holds more than one attachment: index 0 is the first upload, and
    it is the same file ``resourceUrl`` already resolves to, so the learner page
    can keep serving ``resourceUrl`` as the primary document and read the rest
    from here.

    A component authored before the list existed has no list at all, and nothing
    re-saves a component on its own -- so the single file already resolved
    stands in for it and the page behaves exactly as it did.
    """
    stored = (settings or {}).get("componentFiles")
    if isinstance(stored, str):
        try:
            stored = json.loads(stored) if stored.strip() else []
        except (TypeError, ValueError):
            stored = []
    files = []
    seen = set()
    if isinstance(stored, list):
        for entry in stored:
            if not isinstance(entry, dict):
                continue
            url = _s(entry.get("url"))
            # Same rule the authoring side enforces: our own upload path or a
            # plain web link. Anything else is not something to hand a learner.
            if not url.startswith(_COMPONENT_FILE_URL_PREFIXES) or url in seen:
                continue
            seen.add(url)
            files.append({
                "fileName": _s(entry.get("fileName")) or None,
                "url": url,
                "size": entry.get("size") if isinstance(entry.get("size"), (int, float)) else 0,
                "contentType": _s(entry.get("contentType")) or None,
            })
    if files:
        return files
    return [{"fileName": file_name, "url": resource_url, "size": 0, "contentType": None}] if resource_url else []


def _cohort_schedule(cohort_name, programme_name, cohort_id=""):
    """The dates the learner's cohort runs to, or an empty dict.

    Gateway is not something a learner reaches by finishing their modules early:
    the practical period runs to the cohort's end date, and only then does
    Gateway open and the EPA window (``epa_months``) begin. The learner page
    needs those dates to say "modules complete, Gateway opens in March" rather
    than declaring someone Gateway-ready a year ahead of their cohort.

    The cohort is found by ``cohort_id`` when the learner's placement holds one,
    so renaming the cohort does not take its dates off the learner page.
    """
    cohort_name, cohort_id = _s(cohort_name), _s(cohort_id)
    if not cohort_name and not cohort_id:
        return {}
    columns = """
                SELECT start_date, end_date, apprenticeship_end_date,
                       apprenticeship_end_override, epa_months
                FROM curriculum.cohorts
    """
    try:
        with connections["default"].cursor() as cur:
            if cohort_id:
                cur.execute(columns + " WHERE cohort_id = %s AND deleted_at IS NULL", [cohort_id])
            else:
                cur.execute(
                    columns + """
                    WHERE cohort_name = %s
                      AND (%s = '' OR programme_name = %s)
                      AND deleted_at IS NULL
                    ORDER BY updated_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    [cohort_name, _s(programme_name), _s(programme_name)],
                )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not read cohort schedule for %r: %s", cohort_name, exc)
        return {}
    if not row:
        return {}
    start, end, epa_end, epa_end_override, epa_months = row
    return {
        "cohortStartDate": _iso_date(start),
        # The practical period's last day. Gateway opens once it has passed.
        "gatewayStartDate": _iso_date(end),
        "epaEndDate": _iso_date(epa_end_override or epa_end),
        "epaMonths": epa_months if isinstance(epa_months, int) else None,
    }


def _iso_date(value):
    if not value:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else _s(value)[:10]


def _apply_cohort_schedule(detail, source):
    """Add the cohort's dates to a learner payload, without failing the page."""
    detail.update({
        "cohortStartDate": "", "gatewayStartDate": "", "epaEndDate": "", "epaMonths": None,
    })
    from .placement_links import placement_ids

    try:
        cohort_id = placement_ids(source)["cohort_id"]
    except DatabaseError as exc:
        logger.warning("Could not read the placement ids for the cohort schedule: %s", exc)
        cohort_id = ""
    detail.update(_cohort_schedule(getattr(source, "cohort", ""), getattr(source, "programme", ""), cohort_id))


from .point_structure import POINT_SETTINGS_SQL, point_projection
from .audio_alternative import AUDIO_ALTERNATIVE_SETTINGS_SQL, audio_alternative_projection
from .linked_recordings import (LINK_KEY, LINK_MODULE_KEY, LINKED_RECORDING_SETTINGS_SQL, MODULE_KEY,
                                read_remote_sessions, remote_links, resolve_linked_recordings)


def _resolve_from_master(modules, weeks, components, assigned_modules=None, *, compact=False,
                         live_session_ids=True, expected_otjh=False, strict=False, week_ids=None, identities_only=False,
                         resolved_metadata=None, routing_only=False, compact_content=False, catalogue_facts=False):
    """Rebuild the module -> week -> component tree LIVE from the master
    authoring tables (curriculum.modules/weeks/components) so coach edits in the
    Module Builder show up immediately for already-enrolled learners.

    Membership at the MODULE level comes from the learner's source assignments
    (including modules with no children yet). Within each of those modules, the
    weeks + components — their titles, order, and set — come live from master,
    keyed by the assigned module ids. So renames propagate, and
    weeks/components added or removed in Module Builder appear/disappear here.

    Only structured-plan (id-bearing) modules are resolved. Legacy id-less
    entries pass through unchanged and rely on the existing title-matching
    fallbacks (_otjh_by_legacy_title / _resolve_week_ids). Mixed plans are
    handled module-by-module. On any DB error the snapshot tree is returned
    unchanged — never 500 the page because master is unreachable.
    """
    # Read membership from the source assignments even when an assigned module
    # has no weeks/components yet. Flattening its snapshot loses that module id.
    if assigned_modules == []:
        return [], [], []
    module_ids = list(dict.fromkeys(
        _s(entry.get("moduleId")) for entry in (assigned_modules or [])
        if isinstance(entry, dict) and _s(entry.get("moduleId"))
    ))
    for w in weeks:
        mid = w.get("moduleId")
        if assigned_modules is None and mid and mid not in module_ids:
            module_ids.append(mid)
    for c in components:
        mid = c.get("moduleId")
        if assigned_modules is None and mid and mid not in module_ids:
            module_ids.append(mid)

    if not module_ids:
        return modules, weeks, components  # fully legacy plan — nothing to resolve

    live_session_fallbacks = {}
    shared_live_delivery = {}
    try:
        with connections["enrolment"].cursor() as cur:
            if resolved_metadata is not None:
                master_module_title = {mid: title for mid, title in resolved_metadata['module_titles'].items() if mid in module_ids}
                master_weeks = [row for row in resolved_metadata['weeks'] if row[1] in master_module_title]
            else:
                cur.execute(
                    "SELECT m.module_catalogue_id, m.title FROM curriculum.modules m "
                    "WHERE m.module_catalogue_id = ANY(%s) "
                    # An assignment is a learner-owned snapshot. Keep content that
                    # was cascade-hidden with its parent programme available to an
                    # already-assigned learner; a row deleted on its own remains
                    # excluded. ``deleted_via_parent`` distinguishes the two cases.
                    "AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent, '') <> '')",
                    [module_ids],
                )
                master_module_title = {mid: title for mid, title in cur.fetchall()}

                cur.execute(
                    "SELECT id, module_catalogue_id, title, week_number, display_order "
                    "FROM curriculum.weeks WHERE module_catalogue_id = ANY(%s) "
                    f"AND {visible_week_sql('weeks')} "
                    "ORDER BY module_catalogue_id, display_order, week_number, id",
                    [module_ids],
                )
                master_weeks = cur.fetchall()  # [(week_id, module_id, title, week_number, display_order)]

            settings_sql = (
                "CASE WHEN jsonb_typeof(settings_json::jsonb) = 'object' "
                "THEN settings_json::jsonb - 'lessonContent' - 'speakerNotes' "
                "ELSE settings_json::jsonb END"
            )
            # Python str.strip's exact Unicode whitespace set, used by _s.
            strip_chars = ("\t\n\v\f\r \x1c\x1d\x1e\x1f\x85\xa0\u1680" +
                           ''.join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000")
            # Preserve absent versus explicitly cleared builder quiz links in
            # narrow projections too; only absent links use the legacy table.
            quiz_settings_sql = ("CASE WHEN settings_json::jsonb ? 'linkedQuizId' THEN "
                                 "jsonb_build_object('linkedQuizId',settings_json->'linkedQuizId') "
                                 "ELSE '{}'::jsonb END")
            if identities_only:
                # Dashboard rails need identities and quiz links only. Preserve
                # the removed-video rule without transferring lesson bodies.
                video_keys = tuple(dict.fromkeys((*_RESOURCE_URL_KEYS, 'videoUrl', 'embedCode', 'podcastUrl',
                    'audioUrl', 'uploadedFileUrl', 'liveSessionUrl', 'teamsMeetingUrl',
                    'recordingSeriesId', 'recordingSessionNumber', 'recordingDate')))
                video_sql = ','.join(f"'{key}',settings_json->'{key}'" for key in video_keys)
                settings_sql = quiz_settings_sql + " || " + (
                    "CASE WHEN lower(replace(btrim(type),'-','_'))='video' THEN "
                    f"jsonb_build_object({video_sql},'readingContent',CASE WHEN "
                    f"coalesce(btrim(settings_json->>'readingContent','{strip_chars}'),'')<>'' THEN 'content' ELSE '' END) "
                    "ELSE '{}'::jsonb END")
                if catalogue_facts:
                    # My Learning's aggregate read needs live-session action
                    # identities, not lesson bodies or KSB/assignment metadata.
                    keys = ('sessionDate', 'sessionTime', 'sessionDateTimeUtc', 'teamsLiveSessionId',
                            'teamsSessionNumber', 'teamsOccurrenceId', 'extraTeamsMeetingUrl', 'extraTeamsLiveSessionId')
                    settings_sql += " || CASE WHEN lower(replace(btrim(type),'-','_'))='live_session' THEN jsonb_build_object(" + ','.join(
                        f"'{key}',settings_json->'{key}'" for key in keys) + ") ELSE '{}'::jsonb END"
            if compact and not identities_only:
                # List pages need to know whether a reading can be opened,
                # not download every lesson's HTML. The runner reads its one
                # lesson separately, still scoped to this learner's modules.
                settings_sql = (
                    f"({settings_sql} - 'readingContent') || jsonb_build_object("
                    "'_readingContentAvailable', coalesce(regexp_replace("
                    "settings_json->>'readingContent', '<[^>]*>|&nbsp;|\\s', '', 'g'), '') <> '')"
                )
            description_sql = "''" if identities_only else "description"
            if week_ids is not None and not identities_only:
                from .weekly_learning import _JS_SPACE
                # Description is used only as a has-text gate. Match _has_text
                # exactly (remove tags first, then &nbsp; and JS whitespace).
                noise = ('&nbsp;|[' + _JS_SPACE + ']').replace("'", "''")
                description_sql = ("CASE WHEN regexp_replace(regexp_replace(coalesce(description,''),"
                    "'<[^>]*>','','g'),'" + noise + "','','g')<>'' THEN 'content' ELSE '' END")
                keys = tuple(dict.fromkeys((*_RESOURCE_URL_KEYS, 'videoUrl', 'embedCode',
                    'podcastUrl', 'audioUrl', 'liveSessionUrl', 'teamsMeetingUrl', 'teamsLiveSessionId',
                    'teamsSessionNumber', 'recordingSeriesId', 'recordingSessionNumber', 'recordingDate',
                    'sessionDate', 'sessionTime', 'sessionDateTimeUtc', 'durationMinutes',
                    'learnerTitle', 'isMonthlyAssignment', 'assignmentTopics', 'assignmentBrief',
                    'assignmentContent', 'submissionInstructions', 'assignmentFileUrl', 'assignmentFileName',
                    'uploadedFileName', 'reflectionPrompt', 'podcastReflectionQuestion', 'readingReflectionPrompts',
                    'learnerGuidance', 'sourceType', 'resourceSource', 'fileSource')))
                settings_sql = quiz_settings_sql + " || jsonb_build_object(" + ','.join(f"'{key}',settings_json->'{key}'" for key in keys) + ") || " + (
                    "jsonb_build_object('_readingContentAvailable',coalesce(regexp_replace("
                    "settings_json->>'readingContent','<[^>]*>|&nbsp;|\\s','','g'),'')<>'')")

            if compact_content:
                # Dashboard summaries consume identity, title, timing and KSBs,
                # never contentAvailable. Avoid parsing every lesson body merely
                # to compute that unused boolean. Keep every URL used by the
                # removed-video membership rule, and keep quiz/recording links.
                description_sql = "''"
                # One JSON operation avoids repeatedly decoding large authored
                # settings for individual keys. URL presence (the video removal
                # rule), titles, quiz identity and all scheduling settings stay.
                settings_sql = ("CASE WHEN jsonb_typeof(settings_json::jsonb)='object' THEN "
                    "settings_json::jsonb - ARRAY['lessonContent','speakerNotes','readingContent',"
                    "'assignmentTopics','assignmentBrief','assignmentContent','submissionInstructions',"
                    "'componentFiles','reflectionPrompt','podcastReflectionQuestion',"
                    "'readingReflectionPrompts','learnerGuidance','legacySettings'] "
                    f"ELSE ({settings_sql}) END")

            # Presentation metadata survives all compact/identity paths.
            settings_sql = f"({settings_sql}) || {POINT_SETTINGS_SQL} || {AUDIO_ALTERNATIVE_SETTINGS_SQL} || {LINKED_RECORDING_SETTINGS_SQL}"

            detail_columns = (f"CASE WHEN coalesce(btrim(live_sessions_link,'{strip_chars}'),'')<>'' THEN 'content' END AS live_sessions_link, display_order, NULL AS ksb_mappings, "
                              "false AS reflection_required, NULL AS \"Reflection_Question\", false AS tutor_validation_required") if identities_only else (
                              "live_sessions_link, display_order, ksb_mappings, reflection_required, \"Reflection_Question\", tutor_validation_required")
            schedule_column = ''
            if catalogue_facts and resolved_metadata is not None and 'schedule_components' in resolved_metadata:
                from .builder_activity_dates import COMPONENT_SCHEDULE_SETTINGS
                # Keep ->> text semantics for dates independent of the native
                # identity projection's JSON types and display overrides.
                schedule_column = ', ' + COMPONENT_SCHEDULE_SETTINGS.replace('c.settings_json', 'settings_json')
            cur.execute(
                "SELECT id, week_id, module_catalogue_id, type, title, "
                f"{description_sql} AS description, "
                f"{settings_sql} AS settings_json, "
                f"{detail_columns}"
                f"{', expected_otjh' if expected_otjh else ''}{schedule_column} "
                "FROM curriculum.components WHERE module_catalogue_id = ANY(%s) "
                f"{'AND week_id = ANY(%s) ' if week_ids is not None else ''}"
                f"AND {visible_child_sql('components')} "
                + (f"AND (week_id IS NULL OR EXISTS (SELECT 1 FROM curriculum.weeks visible_parent "
                   f"WHERE visible_parent.id=components.week_id "
                   f"AND visible_parent.module_catalogue_id=components.module_catalogue_id "
                   f"AND {visible_week_sql('visible_parent')})) " if catalogue_facts else '') +
                "ORDER BY week_id, display_order, id",
                [module_ids, *([week_ids] if week_ids is not None else [])],
            )
            master_components = cur.fetchall()
            if catalogue_facts and resolved_metadata is not None and 'schedule_components' in resolved_metadata:
                # Preserve the date reader's ordering and module-level
                # components; empty weeks are still supplied independently.
                resolved_metadata['schedule_components'] = [
                    (row[0], row[2], row[1], row[4], row[3], row[-1])
                    for row in sorted(master_components,
                        key=lambda row: (row[2], row[8] is None, row[8], row[0]))]

            # Quiz components are linked either through settings_json.linkedQuizId
            # (the Module Builder save format) or the normalized link table.
            # Resolve both so learner quiz pages receive isQuiz + quizMeta instead
            # of treating a linked quiz as an ordinary generic component.
            quiz_id_by_component = {}
            authored_quiz_component_ids = set()
            fallback_module_ids = set()
            component_ids = [row[0] for row in master_components]
            for comp_id, _week_id, mid, ctype, _ctitle, _cdesc, settings, _live_link, _order, _ksb_mappings, _reflection_required, _reflection_question, _tutor_validation, *_otjh in master_components:
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings) if settings else {}
                    except (ValueError, TypeError):
                        settings = {}
                if not isinstance(settings, dict):
                    settings = {}
                if 'linkedQuizId' in settings:
                    authored_quiz_component_ids.add(comp_id)
                linked_quiz_id = settings.get("linkedQuizId")
                try:
                    if linked_quiz_id not in (None, ""):
                        quiz_id_by_component[comp_id] = int(linked_quiz_id)
                except (TypeError, ValueError):
                    pass
                if (
                    _s(ctype).strip().lower().replace("-", "_") == "live_session"
                    and _s(settings.get("sessionDate"))
                    and not (
                        (_s(settings.get("extraTeamsMeetingUrl")) and _s(settings.get("extraTeamsLiveSessionId")))
                        or (_s(settings.get("teamsLiveSessionId")) and settings.get("teamsSessionNumber"))
                    )
                ):
                    fallback_module_ids.add(_s(mid))

            quiz_meta_by_id = dict((resolved_metadata or {}).get('quiz_meta', {}))
            batched_links = None
            if (catalogue_facts and not (resolved_metadata is not None and 'quiz_links' in resolved_metadata)
                    and any(row[3] == 'quiz' for row in master_components)):
                # The normalized links and their small quiz metadata formerly
                # required two remote SELECTs. Keep the original reader for
                # ambiguous multiply-linked components, with its tie behavior.
                cur.execute("SELECT link.component_id,link.quiz_id,q.id,q.title,q.questions,q.duration,q.time_unit "
                    "FROM curriculum.quiz_component_links link "
                    "JOIN curriculum.components component ON component.id=link.component_id "
                    "LEFT JOIN curriculum.quizzes q ON q.id=link.quiz_id "
                    "WHERE component.module_catalogue_id=ANY(%s) AND component.id=ANY(%s) "
                    f"AND component.type='quiz' AND {visible_child_sql('component')}", [module_ids, component_ids])
                joined = cur.fetchall()
                if len({row[0] for row in joined}) == len(joined):
                    batched_links = [(row[0], row[1]) for row in joined]
                    quiz_meta_by_id.update({row[2]: {'quizId':row[2], 'title':row[3], 'questions':row[4],
                        'duration':row[5], 'timeUnit':row[6]} for row in joined
                        if row[2] is not None and row[2] not in quiz_meta_by_id})
            if batched_links is not None:
                quiz_id_by_component.update((cid, qid) for cid, qid in batched_links
                                            if cid not in authored_quiz_component_ids)
            elif resolved_metadata is not None and 'quiz_links' in resolved_metadata:
                quiz_id_by_component.update({cid: qid for cid, qid in resolved_metadata['quiz_links'].items()
                                             if cid in component_ids and cid not in authored_quiz_component_ids})
            elif component_ids and compact_content:
                # Reuse identities that passed the module and visibility
                # predicates above instead of scanning components again.
                quiz_component_ids = [row[0] for row in master_components if row[3] == 'quiz']
                if quiz_component_ids:
                    cur.execute('SELECT component_id,quiz_id FROM curriculum.quiz_component_links '
                                'WHERE component_id=ANY(%s)', [quiz_component_ids])
                    quiz_links = cur.fetchall()
                    if len({row[0] for row in quiz_links}) != len(quiz_links):
                        # The legacy reader has no explicit tie-break for
                        # multiply-linked components. Keep its original query
                        # for those records rather than choosing a new winner.
                        cur.execute('SELECT link.component_id, link.quiz_id '
                            'FROM curriculum.quiz_component_links link '
                            'JOIN curriculum.components component ON component.id = link.component_id '
                            "WHERE component.module_catalogue_id = ANY(%s) "
                            "AND component.id = ANY(%s) AND component.type = 'quiz' "
                            f"AND {visible_child_sql('component')}", [module_ids, component_ids])
                        quiz_links = cur.fetchall()
                    for component_id, quiz_id in quiz_links:
                        if component_id not in authored_quiz_component_ids:
                            quiz_id_by_component[component_id] = quiz_id
            elif component_ids and (not catalogue_facts or any(row[3] == 'quiz' for row in master_components)):
                cur.execute(
                    "SELECT link.component_id, link.quiz_id "
                    "FROM curriculum.quiz_component_links link "
                    "JOIN curriculum.components component ON component.id = link.component_id "
                    "WHERE component.module_catalogue_id = ANY(%s) "
                    "AND component.id = ANY(%s) AND component.type = 'quiz' "
                    f"AND {visible_child_sql('component')}",
                    [module_ids, component_ids],
                )
                for component_id, quiz_id in cur.fetchall():
                    if component_id not in authored_quiz_component_ids:
                        quiz_id_by_component[component_id] = quiz_id

            linked_quiz_ids = sorted(set(quiz_id_by_component.values()))
            missing_quiz_ids = [qid for qid in linked_quiz_ids if qid not in quiz_meta_by_id]
            if missing_quiz_ids:
                cur.execute(
                    "SELECT id, title, questions, duration, time_unit FROM curriculum.quizzes "
                    "WHERE id = ANY(%s)",
                    [missing_quiz_ids],
                )
                quiz_meta_by_id.update({
                    row[0]: {
                        "quizId": row[0],
                        "title": row[1],
                        "questions": row[2],
                        "duration": row[3],
                        "timeUnit": row[4],
                    }
                    for row in cur.fetchall()
                })

            if identities_only:
                out_weeks = [{'module': _s(master_module_title.get(mid)), 'week': _s(title) or f'Week {number}',
                              'moduleId': mid, 'weekId': wid} for wid, mid, title, number, _ in master_weeks
                             if mid in master_module_title]
                week_lookup = {(row['moduleId'], row['weekId']): row for row in out_weeks}
                valid = set(week_lookup)
                out_components = []
                for cid, wid, mid, ctype, title, _, settings, link, *_ in master_components:
                    if (mid, wid) not in valid:
                        continue
                    if isinstance(settings, str):
                        settings = json.loads(settings)
                    settings = settings if isinstance(settings, dict) else {}
                    if (catalogue_facts and resolved_metadata is not None
                            and 'live_credit_components' in resolved_metadata
                            and _s(ctype).strip().lower().replace('-', '_') == 'live_session'):
                        # Same raw identities as read_live_credits' component
                        # SELECT, before display-only extra-session overrides.
                        resolved_metadata['live_credit_components'].append({
                            'componentId':cid,'moduleId':mid,'title':title,'type':'live_session',
                            **{key:(value if value is None or isinstance(value,str) else json.dumps(value))
                               for key in ('teamsOccurrenceId','teamsLiveSessionId','teamsSessionNumber','sessionDate')
                               for value in [settings.get(key)]}})
                    quiz = quiz_meta_by_id.get(quiz_id_by_component.get(cid))
                    if _s(ctype).strip().lower().replace('-', '_') == 'video' and not (
                        _video_url_from_settings(settings) or quiz or _s(settings.get('readingContent'))
                        or _component_resource_url(settings) or component_audio_url(settings, ctype)
                        or _s(link) or _s(settings.get('liveSessionUrl')) or _s(settings.get('teamsMeetingUrl'))
                        or (_s(settings.get('recordingSeriesId')) and settings.get('recordingSessionNumber')
                            and _s(settings.get('recordingDate')))):
                        continue
                    week = week_lookup[(mid, wid)]
                    out_components.append({**week, 'componentId': cid, 'isQuiz': bool(quiz),
                                           'quizMeta': quiz, 'component': title, **point_projection(settings),
                                           **({'type': ctype, 'sessionDate': _s(settings.get('sessionDate')) or None,
                                               'sessionDateTimeUtc': _s(settings.get('sessionDateTimeUtc')) or None,
                                               'teamsLiveSessionId': (_s(settings.get('extraTeamsLiveSessionId'))
                                                   if _s(settings.get('extraTeamsMeetingUrl')) else '')
                                                   or _s(settings.get('teamsLiveSessionId')) or None} if catalogue_facts else {})})
                titles = list(dict.fromkeys(_s(value) for value in master_module_title.values()))
                if catalogue_facts:
                    # Preserve the same id-less rows as the full reader in a
                    # mixed native/legacy plan. They have no master ID to query.
                    legacy_titles = list(dict.fromkeys(w.get('module') for w in weeks if not w.get('moduleId') and w.get('module')))
                    titles.extend(title for title in legacy_titles if title not in titles)
                    out_weeks.extend(w for w in weeks if not w.get('moduleId') and w.get('module') in legacy_titles)
                    out_components.extend(c for c in components if not c.get('moduleId') and c.get('module') in legacy_titles)
                return titles, out_weeks, out_components

            # Authored KSB weight per component. Used to calculate and display
            # weighted KSB progress; it does not block activity completion.
            ksbs_by_component = {}
            for comp_id, _week_id, _mid, _ctype, _ctitle, _cdesc, _settings, _live_link, _order, ksb_mappings, _reflection_required, _reflection_question, _tutor_validation, *_otjh in master_components:
                items = _component_ksb_items(ksb_mappings)
                if items:
                    ksbs_by_component[comp_id] = items

            # The individual KSBs authored against each component. The learner
            # no longer picks KSBs by hand on completion — these are applied
            # automatically (see components.py), so the UI shows what will be
            # credited rather than asking.
            if component_ids and not routing_only:
                ksb_description = 'NULL AS ksb_description' if compact_content else 'mapping.ksb_description'
                component_scope = ('WHERE mapping.component_id=ANY(%s) '
                    if compact_content else
                    'JOIN curriculum.components component ON component.id = mapping.component_id '
                    'WHERE component.module_catalogue_id = ANY(%s) AND component.id = ANY(%s) '
                    f"AND {visible_child_sql('component')} ")
                cur.execute(
                    f"SELECT mapping.component_id, mapping.ksb_code, {ksb_description}, "
                    "mapping.classification, mapping.weight, mapping.weight_class "
                    "FROM curriculum.ksb_mappings mapping "
                    + component_scope +
                    f"AND {visible_child_sql('mapping')} "
                    "ORDER BY mapping.component_id, mapping.ksb_code",
                    [component_ids] if compact_content else [module_ids, component_ids],
                )
                for comp_id, code, description, classification, weight, weight_class in cur.fetchall():
                    if comp_id in ksbs_by_component:
                        continue
                    ksbs_by_component.setdefault(comp_id, []).append({
                        "code": _s(code),
                        "description": _s(description) or None,
                        "classification": _s(classification) or None,
                        "weight": float(weight or 0),
                        "weightClass": _normalise_weight_class(weight_class, classification),
                    })
            ksb_weight_by_component = {
                component_id: (sum(float(item.get("weight") or 0) for item in items), len(items))
                for component_id, items in ksbs_by_component.items()
            }
            # live_session_ids=False skips the Teams-series lookup for readers
            # that never show teamsLiveSessionId / teamsSessionNumber.
            if fallback_module_ids and live_session_ids:
                from .shared_live_delivery import read_shared_live_delivery
                shared_live_delivery = read_shared_live_delivery(fallback_module_ids, component_ids, cursor=cur)
                from .attendance_lectures import SHARED_TEAMS_SOURCES_SQL
                shared_cte = SHARED_TEAMS_SOURCES_SQL.rsplit('SELECT DISTINCT module_id,source_id FROM shared', 1)[0]
                cur.execute(
                    shared_cte + ", targets AS ("
                    "SELECT module_catalogue_id AS module_id,module_catalogue_id AS source_id "
                    "FROM curriculum.modules WHERE module_catalogue_id=ANY(%s) "
                    "UNION SELECT module_id,source_id FROM shared) "
                    "SELECT DISTINCT target.module_id, s.id, s.timezone, o.id, "
                    "o.session_number, o.scheduled_start "
                    "FROM targets target JOIN curriculum.live_sessions s ON s.module_catalogue_id=target.source_id "
                    "JOIN curriculum.live_session_occurrences o ON o.live_session_id = s.id "
                    "WHERE lower(btrim(s.status)) NOT IN ('deleted', 'superseded', 'failed', 'cancelled', 'canceled') "
                    "AND lower(btrim(o.status)) NOT IN ('deleted', 'superseded', 'cancelled', 'canceled', 'failed') "
                    "ORDER BY target.module_id, o.scheduled_start, o.session_number, o.id",
                    [sorted(fallback_module_ids), sorted(fallback_module_ids)],
                )
                live_session_fallbacks = _live_session_occurrence_fallbacks(cur.fetchall())
    except DatabaseError as exc:
        logger.warning("Could not live-resolve training plan from master: %s", exc)
        if strict:
            raise
        return modules, weeks, components

    # Group master rows by parent.
    weeks_by_module = {}
    for week_id, mid, title, week_number, _order in master_weeks:
        live_title = _s(title) or f"Week {week_number}"
        weeks_by_module.setdefault(mid, []).append((week_id, live_title))

    comps_by_week = {}
    for comp_id, week_id, _mid, ctype, ctitle, cdesc, settings, stored_live_link, _order, _ksb_mappings, reflection_required, reflection_question, tutor_validation_required, *component_otjh in master_components:
        # settings_json comes back from the raw cursor as a JSON string (JSONField
        # auto-parsing only happens through the ORM), so parse it here.
        if isinstance(settings, str):
            try:
                settings = json.loads(settings) if settings else {}
            except (ValueError, TypeError):
                settings = {}
        if not isinstance(settings, dict):
            settings = {}
        delivery = shared_live_delivery.get(comp_id)
        if delivery:
            # The builder and join endpoint pair shared live cards by authored
            # position. Its source's booked identity, not the alias's stale
            # date, determines which occurrence this card opens.
            settings = {**settings, **delivery}
        live_session_url = (
            _s(delivery.get('liveSessionUrl')) if delivery else _s(stored_live_link)
        ) or (
            _s(settings.get("liveSessionUrl"))
            or _s(settings.get("teamsMeetingUrl"))
            or None
        )
        normalised_type = _s(ctype).strip().lower().replace("-", "_")
        fallback = (
            live_session_fallbacks.get((_s(_mid), _s(settings.get("sessionDate"))[:10]))
            if normalised_type == "live_session" else None
        )
        stored_series_id = _s(settings.get("teamsLiveSessionId"))
        extra_series_id = (
            _s(settings.get("extraTeamsLiveSessionId"))
            if normalised_type == "live_session" and _s(settings.get("extraTeamsMeetingUrl")) else ""
        )
        if extra_series_id:
            # An additional week meeting owns its own one-off series. The
            # inherited module series must not supply this card's results.
            teams_live_session_id = extra_series_id
            teams_session_number = 1
        elif fallback and (not stored_series_id or stored_series_id == fallback["teamsLiveSessionId"]):
            teams_live_session_id = stored_series_id or fallback["teamsLiveSessionId"]
            teams_session_number = settings.get("teamsSessionNumber") or fallback["teamsSessionNumber"]
        else:
            teams_live_session_id = stored_series_id or None
            teams_session_number = settings.get("teamsSessionNumber") or None
        # Use the shared resolver, not a bare settings["videoUrl"] read: a video
        # can be authored as an embed snippet instead of a plain URL, and the
        # removal check below must not treat that as "video removed".
        video_url = _video_url_from_settings(settings)
        # Generalised content payload per component type (mirrors the authoring
        # settings_json keys in the Module Builder). Lets the learner open a
        # podcast / reading / slide deck / reflection the same way as a video.
        # Podcast audio may be an external listening-page link (podcastUrl) or an
        # uploaded file (uploadedFileUrl) — either can be a real audio source.
        #
        audio_url = component_audio_url(settings, ctype)
        content_html = _s(settings.get("readingContent")) or None
        file_name = (
            _s(settings.get("fileName"))
            or _s(settings.get("uploadedFileName"))
            # Paired with assignmentFileUrl below — a row carrying only the
            # assignment-specific keys would otherwise show its document with
            # no name on it.
            or _s(settings.get("assignmentFileName"))
            or None
        )
        download_allowed = bool(settings.get("downloadAllowed"))
        # An assignment has no description column of its own — the brief the
        # author writes *is* its description, and it lives only in settings.
        # The two authoring surfaces store it differently: the Module Builder's
        # plain TextArea writes `assignmentBrief`, while the Week Builder's rich
        # text editor writes HTML into `assignmentContent`. Keep them apart so
        # the learner page can escape the plain one and render the marked-up
        # one, instead of printing raw tags at the learner.
        #
        # Saving in either builder mirrors whichever key is set into the other
        # (see normaliseComponentSettings), so `assignmentContent` routinely
        # holds a *copy of the plain text* rather than markup. Only treat it as
        # rich text when it actually carries tags — otherwise a plain brief
        # would render through innerHTML and lose its line breaks.
        assignment_brief = _s(settings.get("assignmentBrief")) or None
        assignment_content = _s(settings.get("assignmentContent")) or None
        assignment_brief_html = (
            assignment_content
            if assignment_content and _HTML_TAG_RE.search(assignment_content)
            else None
        )
        # A file-tab assignment has no written brief at all; fall back to the
        # mirrored copy so the text still reaches the learner either way.
        if not assignment_brief and assignment_content and not assignment_brief_html:
            assignment_brief = assignment_content
        # Filtered per key, not on the coalesced result: the boilerplate is
        # seeded into `reflectionPrompt` on every component, so it always wins
        # the chain and would hide real guidance authored in one of the later
        # keys. Dropping it per key lets that authored text through.
        reflection_prompt = (
            _authored_reflection_prompt(_s(settings.get("reflectionPrompt")))
            or _authored_reflection_prompt(_s(settings.get("podcastReflectionQuestion")))
            or _authored_reflection_prompt(_s(settings.get("readingReflectionPrompts")))
            or _authored_reflection_prompt(_s(settings.get("learnerGuidance")))
            or None
        )
        # PowerPoint (presentationUrl / uploadedFileUrl) and any other component
        # with an attached link/file all resolve to the same resourceUrl field,
        # picking the one the author actually chose — see _component_resource_url.
        resource_url = _component_resource_url(settings)
        # An assignment's attached document has its own pair of keys, which the
        # generic resolution above does not know. The Module Builder writes them
        # alongside `uploadedFileUrl` today, but rows authored before that
        # dual-write carry the file *only* here — and the client-side mirroring
        # that would repair them never runs unless someone re-saves the
        # component, so the learner API has to read them directly or the
        # curriculum team's file is invisible to the learner.
        if normalised_type == "assignment":
            resource_url = resource_url or _s(settings.get("assignmentFileUrl")) or None
        duration = settings.get("durationMinutes")
        ksb_weight, ksb_count = ksb_weight_by_component.get(comp_id, (0.0, 0))
        linked_quiz = quiz_meta_by_id.get(quiz_id_by_component.get(comp_id))
        # A video component whose video the author has since removed has nothing
        # left for the learner to do, so drop it from the live tree exactly as a
        # deleted component is dropped — rather than showing an empty player.
        # Only the tree is affected: completions already recorded against this
        # component stay in "Training_plan_progress" and keep their credit,
        # because progress is read from that log by componentId, not from here.
        #
        # Guarded on the other content a video component can carry, so removing
        # the video from one that also has, say, a linked quiz or a reading body
        # hides the player without hiding the remaining work.
        recording_series_id = _s(settings.get("recordingSeriesId"))
        recording_session_number = settings.get("recordingSessionNumber")
        recording_date = _s(settings.get("recordingDate"))
        has_recording_source = bool(recording_series_id and recording_session_number and recording_date)
        if normalised_type == "video" and not video_url and not (
            linked_quiz or content_html or resource_url or audio_url or live_session_url or has_recording_source
        ):
            continue
        from .assignment_topics import topics_from_settings
        from .assignment_settings import flagged_monthly, learner_title
        # The programme team's name for an assignment replaces the (often
        # imported) curriculum title wherever the learner sees it.
        display_title = (normalised_type == "assignment" and learner_title(settings)) or ctitle
        comps_by_week.setdefault(week_id, []).append({
            "componentId": comp_id,
            **point_projection(settings),
            # Only selected (14th column) when the caller asked for expected_otjh.
            "expectedOtjh": float(component_otjh[0]) if component_otjh and component_otjh[0] is not None else None,
            "display": _display_quiz_title(linked_quiz["title"]) if linked_quiz else _display_component_title(ctype, display_title),
            "type": ctype,
            "description": _s(cdesc) or None,
            "assignmentTopics": topics_from_settings(settings) if normalised_type == "assignment" else [],
            # The author's flag only; which assignments are monthly for this
            # learner is decided across their whole plan (assignment_settings).
            "monthlyAssignment": normalised_type == "assignment" and flagged_monthly(settings),
            "assignmentBrief": assignment_brief,
            "assignmentBriefHtml": assignment_brief_html,
            "videoUrl": video_url,
            "audioUrl": audio_url,
            **audio_alternative_projection(settings, normalised_type, audio_url),
            "contentHtml": content_html,
            **({"hasReadingContent": bool(settings.get("_readingContentAvailable"))} if compact else {}),
            "fileName": file_name,
            # Everything attached, in the author's order. `resourceUrl` above is
            # this list's first entry; the rest are new and additive, so a client
            # that does not read `files` keeps showing exactly what it showed.
            "files": _component_files(settings, resource_url, file_name),
            "downloadAllowed": download_allowed,
            "reflectionPrompt": reflection_prompt,
            "reflectionRequired": bool(reflection_required),
            # Authored on the component and, until now, read by nothing. When
            # true the activity must reach a coach, so the learner page cannot
            # let it complete without a reflection to hand in.
            "tutorValidationRequired": bool(tutor_validation_required),
            "reflectionQuestion": _s(reflection_question) or None,
            "resourceUrl": resource_url,
            "liveSessionUrl": live_session_url,
            "teamsLiveSessionId": teams_live_session_id,
            "teamsSessionNumber": teams_session_number,
            "recordingSeriesId": recording_series_id or None,
            "recordingSessionNumber": recording_session_number if has_recording_source else None,
            "recordingDate": recording_date or None,
            # Read by resolve_linked_recordings below; not sent to the client.
            MODULE_KEY: _s(_mid),
            LINK_KEY: _s(settings.get(LINK_KEY)) if normalised_type == "recording_placeholder" else "",
            LINK_MODULE_KEY: _s(settings.get(LINK_MODULE_KEY)) if normalised_type == "recording_placeholder" else "",
            "sessionDate": _s(settings.get("sessionDate")) or None,
            "sessionTime": _s(settings.get("sessionTime")) or None,
            "sessionDateTimeUtc": _s(settings.get("sessionDateTimeUtc")) or None,
            "durationMinutes": duration if isinstance(duration, (int, float)) else None,
            "ksbWeightTotal": ksb_weight,
            "ksbMappingCount": ksb_count,
            "ksbMappings": ksbs_by_component.get(comp_id, []),
            "isQuiz": bool(linked_quiz),
            "quizMeta": ({
                "quizId": linked_quiz["quizId"],
                "questions": linked_quiz["questions"],
                "duration": linked_quiz["duration"],
                "timeUnit": linked_quiz["timeUnit"],
            } if linked_quiz else None),
        })

    # A session recording linked to a live session plays that session's
    # recording, or folds into it entirely (see linked_recordings). Sessions in
    # another group's module are read by id; if that read fails the recording
    # simply stays unresolved rather than failing the learner's whole plan.
    remote_sessions = {}
    linked_elsewhere = remote_links(comps_by_week)
    if linked_elsewhere:
        try:
            with connections["enrolment"].cursor() as cur:
                remote_sessions = read_remote_sessions(cur, linked_elsewhere)
        except DatabaseError as exc:
            logger.warning("Could not read linked live sessions for session recordings: %s", exc)
    comps_by_week = resolve_linked_recordings(comps_by_week, remote_sessions)

    # Distinct snapshot module ids in first-seen order, plus any legacy
    # (id-less) module titles that must still pass through.
    legacy_module_titles = [
        w.get("module") for w in weeks if not w.get("moduleId") and w.get("module")
    ]

    out_modules, out_weeks, out_components = [], [], []
    seen_modules = set()

    for mid in module_ids:
        if mid not in master_module_title:
            continue  # independently deleted/missing modules stay unavailable
        live_module = _s(master_module_title[mid])
        # The label list is unique for older consumers. Keep every module's
        # weeks/components: the subject workspace groups by moduleId, and two
        # assigned subjects must not lose content because they share a title.
        if not live_module:
            continue
        if live_module not in seen_modules:
            out_modules.append(live_module)
        seen_modules.add(live_module)

        for week_id, live_wk in weeks_by_module.get(mid, []):
            out_weeks.append({
                "module": live_module, "week": live_wk,
                "moduleId": mid, "weekId": week_id,
            })
            for comp in comps_by_week.get(week_id, []):
                out_components.append({
                    "module": live_module, "week": live_wk,
                    "component": comp["display"],
                    "moduleId": mid, "weekId": week_id, "componentId": comp["componentId"],
                    "type": comp["type"],
                    **point_projection(comp),
                    "description": comp["description"],
                    "assignmentTopics": comp["assignmentTopics"],
                    "monthlyAssignment": comp["monthlyAssignment"],
                    "assignmentBrief": comp["assignmentBrief"],
                    "assignmentBriefHtml": comp["assignmentBriefHtml"],
                    "videoUrl": comp["videoUrl"],
                    "audioUrl": comp["audioUrl"],
                    **({"alternativeYouTubeUrl": comp["alternativeYouTubeUrl"]} if comp.get("alternativeYouTubeUrl") else {}),
                    "contentHtml": comp["contentHtml"],
                    **({"hasReadingContent": comp["hasReadingContent"]} if compact else {}),
                    "fileName": comp["fileName"],
                    "downloadAllowed": comp["downloadAllowed"],
                    "reflectionPrompt": comp["reflectionPrompt"],
                    "reflectionRequired": comp["reflectionRequired"],
                    "tutorValidationRequired": comp["tutorValidationRequired"],
                    "reflectionQuestion": comp["reflectionQuestion"],
                    "resourceUrl": comp["resourceUrl"],
                    "liveSessionUrl": comp["liveSessionUrl"],
                    "teamsLiveSessionId": comp["teamsLiveSessionId"],
                    "teamsSessionNumber": comp["teamsSessionNumber"],
                    "recordingSeriesId": comp["recordingSeriesId"],
                    "recordingSessionNumber": comp["recordingSessionNumber"],
                    "recordingDate": comp["recordingDate"],
                    "sessionDate": comp["sessionDate"],
                    "sessionTime": comp["sessionTime"],
                    "sessionDateTimeUtc": comp["sessionDateTimeUtc"],
                    "durationMinutes": comp["durationMinutes"],
                    "ksbWeightTotal": comp["ksbWeightTotal"],
                    "ksbMappingCount": comp["ksbMappingCount"],
                    "ksbMappings": comp["ksbMappings"],
                    "isQuiz": comp["isQuiz"],
                    "quizMeta": comp["quizMeta"],
                    # Same value _otjh_by_component_id reads, from this row.
                    **({"expectedOtjh": comp["expectedOtjh"]} if expected_otjh else {}),
                })

    # Preserve legacy (id-less) modules unchanged so pre-structured-format
    # learners are unaffected.
    for legacy_title in legacy_module_titles:
        if legacy_title in seen_modules:
            continue
        seen_modules.add(legacy_title)
        out_modules.append(legacy_title)
        out_weeks.extend(w for w in weeks if not w.get("moduleId") and w.get("module") == legacy_title)
        out_components.extend(
            c for c in components if not c.get("moduleId") and c.get("module") == legacy_title
        )

    return out_modules, out_weeks, out_components


def _annotate_otjh(components):
    """Attach expectedOtjh (hours, float|None) to each component dict. Prefers
    an exact componentId match (structured-plan saves); falls back to title
    matching only for components saved before that format existed. Returns
    (annotated_components, total_hours)."""
    if not components:
        return components, 0.0

    otjh_by_id = _otjh_by_component_id(components)
    otjh_by_legacy_key = _otjh_by_legacy_title(components)

    total = 0.0
    for c in components:
        cid = c.get("componentId")
        if cid:
            otjh = otjh_by_id.get(cid)
        else:
            otjh = otjh_by_legacy_key.get((c.get("module"), c.get("week"), c.get("component")))
        c["expectedOtjh"] = otjh
        is_quiz = c.get("isQuiz") or _s(c.get("type")).strip().lower() == "quiz"
        if otjh and not is_quiz:
            total += otjh
    return components, round(total, 2)


def _activity_module_components(components, component_id):
    selected = next((c for c in components if c.get('componentId') == component_id), None)
    if selected is None:
        raise ServiceError("Activity not found in this learner's plan.", status=404)
    module_id = selected.get('moduleId')
    return [c for c in components if (c.get('moduleId') == module_id if module_id else
            not c.get('moduleId') and c.get('module') == selected.get('module'))]


def build_learner_detail(source, pk, *, compact=False, read_only=False, quiz_view=False,
                         activity_component_id=None):
    """The learner's full workspace payload for one already-loaded source row.

    Split out of the view so other callers can serve the same shape behind their
    own authorisation — the employer portal shows an employer their own learner's
    plan, hours and KSBs without reimplementing any of this. Raises DatabaseError;
    callers decide what that means for their response.
    """
    # Date-based activation has no user action of its own. Re-checking here
    # keeps the learner workspace correct between scheduled daily sweeps.
    if not read_only:
        advance_learner(source)
    # Plans selected during enrolment used to contain only module ids. Fill
    # in the authored weeks/components before serialising the learner page,
    # which also repairs learners activated before this behaviour existed.
    if not read_only:
        hydrate_source_training_plan(source)
    assigned_modules = effective_training_plan(source)
    learner_profile = _active_profile_for_source(source, pk, **({'heal': False} if activity_component_id else {}))

    if learner_profile and not learner_profile.ksbs and not read_only:
        try:
            from .active_users import refresh_learner_ksb_snapshot

            if refresh_learner_ksb_snapshot(learner_profile, source):
                learner_profile = _active_profile_for_source(source, pk)
        except DatabaseError as exc:
            logger.warning("Could not refresh learner KSB snapshot for %s: %s", pk, exc)

    detail = to_learner_detail(source, learner_profile)
    # Created_users.Attendance_type is the staff-owned delivery classification.
    # The live-session results view uses it to avoid presenting a Teams absence
    # workflow to learners whose programme is delivered through recordings.
    detail["attendanceType"] = _s(getattr(source, "attendance_type", "")) or None
    # Why the learner cannot start yet, if they cannot. The workspace shows this
    # instead of assuming the answer is always their start date.
    detail["accessGate"] = access_gate(source)
    detail["learningAccess"] = learning_access(source)
    _apply_cohort_schedule(detail, source)
    # Live-resolve titles + membership from the master authoring tables so coach
    # edits in Module Builder reflect here immediately (structured-plan learners).
    detail["modules"], detail["week"], detail["components"] = _resolve_from_master(
        detail["modules"], detail["week"], detail["components"],
        assigned_modules=assigned_modules,
        **({"compact": True} if compact else {}),
    )
    detail["components"] = _apply_programme_assignment_template(
        detail["components"], getattr(source, "programme", "")
    )
    detail["components"], detail["totalExpectedOtjh"] = _annotate_otjh(detail["components"])
    detail["week"], detail["components"] = _append_week_quizzes(
        detail["week"], detail["components"], assigned_modules=assigned_modules,
    )
    # Quiz runners need the plan, progress and reflection requirements, not
    # dashboard totals or their persistence on a GET request.
    snapshot = None if quiz_view or activity_component_id else _live_otjh_snapshot(detail, learner_profile)
    if snapshot is not None:
        _apply_live_otjh_snapshot(detail, snapshot)
    if snapshot is not None and learner_profile is not None and not read_only:
        try:
            persist_live_otjh_snapshot(learner_profile, snapshot)
        except DatabaseError as exc:
            logger.warning("Could not persist hours columns for learner %s: %s", pk, exc)
    # After the hours snapshot so OTJH persistence is unaffected; this only
    # re-attaches earned quiz results to slots an author has since re-linked
    # or removed.
    # The compact workspace projection only needs historical slots that can
    # contribute to completion. Failed attempts remain in quizAttempts, but a
    # potentially huge curriculum-revision scan cannot change their outcome.
    retain_quiz_progress(detail, completed_only=compact)

    # The viewer consumes completion counts for this module and navigation
    # identities elsewhere. Do not resolve attendance/recovery for unrelated
    # modules merely to construct its Next activity link.
    attendance_components = detail['components']
    if activity_component_id:
        attendance_components = _activity_module_components(attendance_components, activity_component_id)

    # Attendance affects activity completion only; the hours snapshot above
    # and every stored progress record remain unchanged.
    from .teams_attendance import annotate_attendance_completion
    annotated = annotate_attendance_completion(attendance_components, learner_profile)
    if activity_component_id:
        by_identity = {id(original): replacement for original, replacement in zip(attendance_components, annotated)}
        detail['components'] = [by_identity.get(id(c), c) for c in detail['components']]
    else:
        detail['components'] = annotated

    # Carry earned completion into an explicitly verified replacement without
    # manufacturing attempts or changing the persisted hours/attendance above.
    from .replacement_learning import annotate_replacement_learning
    detail['components'] = annotate_replacement_learning(detail['components'], pk)

    from .live_session_progress import read_live_credits, supplement_component_progress
    detail['componentProgress'] = supplement_component_progress(
        detail.get('componentProgress') or [], read_live_credits(source, components=(
            _activity_module_components(detail['components'], activity_component_id)
            if activity_component_id else detail['components'])))

    if compact:
        # Nullable optional fields dominate the JSON for large plans. Omission
        # has the same meaning to consumers; keep false, zero and empty strings.
        detail["components"] = [
            {key: value for key, value in component.items()
             if value is not None or key in {"module", "week", "component", "expectedOtjh"}}
            for component in detail["components"]
        ]
    return detail


def _reading_content(source, component_id):
    """Read one lesson without rebuilding the learner's progress graph.

    Match the module/week visibility used by _resolve_from_master; an arbitrary
    component id must never grant access to unassigned or deleted content.
    """
    return _authored_setting(source, component_id, "readingContent", "contentHtml")


def _lesson_content(source, component_id):
    """A video's authored lesson content ("Component content" in the Module
    Builder), read the same scoped way as a reading. List reads strip it; only
    the one opened activity carries it."""
    return _authored_setting(source, component_id, "lessonContent", "lessonContentHtml")


# Settings keys this reader may select, so the key is never caller-supplied SQL.
_AUTHORED_SETTING_KEYS = frozenset({"readingContent", "lessonContent"})


def _authored_setting(source, component_id, setting_key, response_key):
    if setting_key not in _AUTHORED_SETTING_KEYS:
        raise ValueError(f"Unsupported authored setting: {setting_key!r}")
    module_ids = list(dict.fromkeys(
        _s(module.get("moduleId")) for module in effective_training_plan(source)
        if isinstance(module, dict) and _s(module.get("moduleId"))
    ))
    if not module_ids or not component_id:
        return None
    with connections["enrolment"].cursor() as cursor:
        cursor.execute(f'''SELECT c.id, c.settings_json->>'{setting_key}'
            FROM curriculum.components c
            JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
            JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=m.module_catalogue_id
            WHERE c.id=%s AND c.module_catalogue_id=ANY(%s)
              AND ''' + visible_child_sql('c') + '''
              AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent, '') <> '')
              AND ''' + visible_week_sql('w'), [component_id, module_ids])
        row = cursor.fetchone()
    return {"componentId": row[0], response_key: _s(row[1]) or None} if row else None


@learner_self_or_staff(kwarg="pk")
def learner_detail(request, kind, pk):
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        # all_learners, not objects: the default manager is scoped to
        # apprenticeship rows, so a commercial learner would 404 here.
        source = model.all_learners.get(pk=pk)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    try:
        if request.GET.get("content") == "reading":
            reading = _reading_content(source, request.GET.get("component_id"))
            if reading is None:
                return _error("Activity not found in this learner's plan.", 404)
            response = JsonResponse(reading)
            response['Cache-Control'] = 'private, no-store'
            return response
        if request.GET.get("content") == "activity":
            component_id = request.GET.get("component_id")
            if not component_id:
                return _error("An activity identity is required.", 400)
            detail = build_learner_detail(source, pk, compact=True, read_only=True,
                                          activity_component_id=component_id)
            component = next(c for c in detail['components'] if c.get('componentId') == component_id)
            if not component.get('contentHtml') and (component.get('hasReadingContent') or component.get('type') == 'reading'):
                reading = _reading_content(source, component_id)
                if reading is None:
                    return _error("Activity not found in this learner's plan.", 404)
                component['contentHtml'] = reading['contentHtml']
            if _s(component.get('type')).lower().replace('-', '_') == 'video':
                # Shown under the player. A video the curriculum tables cannot
                # place (legacy or replacement learning) still plays without it,
                # so a missing row is not an error here.
                lesson = _lesson_content(source, component_id)
                component['lessonContentHtml'] = (lesson or {}).get('lessonContentHtml')
            response = JsonResponse(detail)
            response['Cache-Control'] = 'private, no-store'
            return response
        options = {"compact": True} if request.GET.get("content") == "summary" else {}
        if request.GET.get("content") == "quiz":
            options = {"compact": True, "read_only": True, "quiz_view": True}
        with measure_projection(
            'learner-detail', kind=kind, learner_id=pk,
            section=request.GET.get('content') or 'complete',
        ) as measurement:
            with measurement.stage('detail'):
                return JsonResponse(build_learner_detail(source, pk, **options))
    except ServiceError as exc:
        return _error(str(exc), exc.status)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)


def _first_session_unlocked(source, status):
    if status.strip().casefold() != "delivery":
        return False
    learner_type = _s(getattr(source, "learner_type", "")) or "apprenticeship"
    if learner_type.casefold() != "apprenticeship":
        return False
    from .learner_progression import learner_signed_compliance_documents

    return learner_signed_compliance_documents(learner_type, source.id)


@learner_self_or_staff(kwarg="pk")
def learner_summary(request, kind, pk):
    """Return the small identity projection used by lightweight learner pages.

    Attendance and similar pages need the learner's heading only. They must not
    pay for the full training-plan/progress graph that ``learner_detail`` builds.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        source = model.all_learners.only(
            "id", "username", "email", "phone_number", "programme",
            "programme_status", "cohort", "group", "employer", "employer_id", "organization",
            "learner_type", "aptem_id", "start_date", "end_date",
            "learner_start_date", "learner_end_date",
            "practical_period_end_date", "apprenticeship_end_date",
            "onboarding_status",
        ).get(pk=pk)
        # A blank status is an account nobody has moved on yet: 'Fresh user',
        # as learner detail and the first-login check already read it. Left
        # blank here, the learner workspace took it for "status unknown" and
        # opened the full dashboard instead of the first-login screens.
        resolved_status = programme_status(source) or DEFAULT_PROGRAMME_STATUS
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    from .apprenticeship_agreement import _group_dates

    start, end, origin = _group_dates(source)
    # access_gate resolves the same programme start; reuse this request's answer
    # instead of reading the cohort again.
    source._resolved_group_dates = (start, end, origin)
    return JsonResponse({
        "id": str(source.id),
        "name": _s(source.username),
        "email": _s(source.email),
        "phone": _s(source.phone_number),
        "programme": _s(source.programme),
        "programmeStatus": resolved_status,
        # 'Submitted' once the learner hands in their enrolment wizard,
        # 'Completed' once staff sign it off; the learner's Reviews unlock then.
        "onboardingStatus": _s(getattr(source, "onboarding_status", "")),
        # A Delivery apprentice's First Learning Session tab unlocks once they
        # have signed all four compliance documents (the sidebar reads this).
        "firstSessionUnlocked": _first_session_unlocked(source, resolved_status),
        "cohort": _s(source.cohort),
        "group": _s(source.group),
        "employer": _s(source.employer),
        "employerId": source.employer_id,
        "organization": _s(getattr(source, "organization", "")),
        "learnerType": _s(getattr(source, "learner_type", "")) or "apprenticeship",
        "isActive": resolved_status.casefold() == "active",
        "programmeStartDate": _iso_date(start),
        # See mappers.learner_detail_payload: the learner's own recorded start,
        # unmediated by the cohort fallback that programmeStartDate applies.
        "learnerStartDate": _s(getattr(source, "learner_start_date", None)),
        # Mirrors learnerStartDate: the learner's own recorded end, straight
        # from Created_users.Learner_end_date. programmeEndDate below resolves
        # through the programme/apprenticeship columns, which stay empty for a
        # learner whose end date is only recorded against themselves.
        "learnerEndDate": _s(getattr(source, "learner_end_date", None)),
        "programmeEndDate": _iso_date(source.end_date or source.practical_period_end_date or source.apprenticeship_end_date or end),
        "accessGate": access_gate(source),
        "learningAccess": learning_access(source),
        "studentActivityAvailable": student_activity_available(getattr(source, "aptem_id", None)),
    })
