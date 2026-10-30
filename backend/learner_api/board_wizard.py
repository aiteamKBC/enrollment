"""Fill the enrolment board from the wizard/ILR tables.

The board's panels were written against the JSON columns on
enrolment."Created_users" (Sub_programme, Tracker, Contacts, Documents, ...).
Those columns are only populated by the delivery-side importers, so for a
learner who came in through the enrolment wizard they are all NULL and every
panel renders empty — even though the same facts were captured during
onboarding and are sitting in enrolment."Extended_ILR" and the per-step
Wizard_* tables.

This module reads those tables and derives the board sections from them. It
only ever *fills gaps*: where a Created_users column already holds data (a
delivery-imported learner) that data wins, because it is the operational
record. See `merge_wizard_sections`.

Everything here is read-only and defensive — a learner part-way through the
wizard has some tables populated and others empty, and a board page must not
500 because of it.
"""
from django.db import DatabaseError

from enrolment_api.models import (
    ExtendedIlr,
    WizardCvJob,
    WizardKsbAssessment,
    WizardPersonalDetails,
    WizardPlr,
    WizardPlrRecord,
    WizardPolicyAck,
)

# The policy list the wizard presents, in order. Acks are stored by index-based
# id (kbc-0, kbc-1, ...) rather than by name, so the label has to be recovered
# from position — a renamed policy keeps its acknowledgement.
POLICY_LABELS = [
    "Safeguarding policy",
    "Prevent duty policy",
    "Equality and diversity policy",
    "Health and safety policy",
    "Data protection / privacy notice",
    "Complaints procedure",
    "Appeals procedure",
    "Learner code of conduct",
    "Attendance policy",
    "Malpractice and maladministration policy",
    "Fees and refunds policy",
]


def _s(value):
    """Coerce to a trimmed string ('' for None)."""
    return "" if value is None else str(value).strip()


def record_address(learner):
    """The learner's home address on their record, as one line.

    The first-sign-in screens (first_login_details) and the staff create form
    both write it to these columns; the wizard's Personal Details holds it as a
    single free-text answer, so it is offered back in that shape. Lines 3 and 4
    are town/city and county.
    """
    parts = (
        getattr(learner, "address_line_1", None),
        getattr(learner, "address_line_2", None),
        getattr(learner, "address_line_3", None),
        getattr(learner, "address_line_4", None),
        getattr(learner, "current_postcode", None),
    )
    return ", ".join(p for p in (_s(part) for part in parts) if p)


#: The learner's reusable signature (sql/2026-09-07_add_signature_to_created_users.sql).
#: Unmapped on EnrolmentUser on purpose -- a mapped column missing from the
#: database breaks every query on the table -- so it is read here by raw SQL.
SAVED_SIGNATURE_COLUMN = "Learner_signature"
SAVED_SIGNATURE_AT_COLUMN = "Learner_signature_saved_at"


def saved_signature(learner_id):
    """``(signature data URL, "YYYY-MM-DD" UK date saved)`` or ``("", "")``.

    The signature a learner agreed to use on the platform when they first signed
    in, offered back so the enrolment wizard does not ask them to sign again.
    Missing columns (the SQL file not applied yet) and any read failure mean
    "no saved signature": the learner can always sign by hand. The savepoint
    keeps a failed probe from poisoning a surrounding transaction, as in
    monthly_reports._saved_signature.
    """
    from zoneinfo import ZoneInfo

    from django.db import connections, transaction

    try:
        with transaction.atomic(using="enrolment"):
            with connections["enrolment"].cursor() as cur:
                cur.execute(
                    f'''select "{SAVED_SIGNATURE_COLUMN}", "{SAVED_SIGNATURE_AT_COLUMN}"
                          from enrolment."Created_users"
                         where id = %s
                         limit 1''',
                    [learner_id],
                )
                row = cur.fetchone()
    except DatabaseError:
        return "", ""
    if not row or not _s(row[0]):
        return "", ""
    saved_at = row[1]
    if hasattr(saved_at, "astimezone"):
        saved_on = saved_at.astimezone(ZoneInfo("Europe/London")).date().isoformat()
    elif hasattr(saved_at, "isoformat"):
        saved_on = saved_at.isoformat()
    else:
        saved_on = ""
    return _s(row[0]), saved_on


def fmt_date(value):
    """Date/datetime/str -> 'DD/MM/YYYY' ('' when absent).

    The board renders dates as plain strings, and the wizard tables mix real
    date columns with free-text ones, so normalise here rather than in the view.
    Also used by to_board for the cohort dates, hence public.
    """
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip()
    # ISO (from a text column) -> display order.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[8:10]}/{text[5:7]}/{text[:4]}"
    return text


# Short alias — this module uses it heavily below.
_fmt_date = fmt_date


def _d(value):
    return value if isinstance(value, dict) else {}


def load_wizard_bundle(learner_id):
    """Every wizard row for one learner, or None if the tables are unreachable.

    Fetched in one place so a board render costs a fixed, small number of
    queries regardless of how many panels end up using the data.
    """
    try:
        lid = int(learner_id)
    except (TypeError, ValueError):
        return None
    try:
        return {
            "ilr": ExtendedIlr.objects.filter(learner_id=lid).first(),
            "personal": WizardPersonalDetails.objects.filter(learner_id=lid).first(),
            "cv_job": WizardCvJob.objects.filter(learner_id=lid).first(),
            "plr": WizardPlr.objects.filter(learner_id=lid).first(),
            "plr_records": list(WizardPlrRecord.objects.filter(learner_id=lid).order_by("id")),
            "policies": list(WizardPolicyAck.objects.filter(learner_id=lid).order_by("id")),
            "ksbs": list(WizardKsbAssessment.objects.filter(learner_id=lid).order_by("ksb_id")),
        }
    except DatabaseError:
        # The board is still useful without the onboarding detail; a missing or
        # not-yet-applied wizard table must not take the whole page down.
        return None


# --------------------------------------------------------------------------- #
# section builders                                                            #
# --------------------------------------------------------------------------- #
def _contacts(bundle):
    """Next of kin + employer line manager as board contact rows.

    These are the two real people captured during onboarding, and the Contacts
    panel is where the console expects to find them.
    """
    answers = _d(bundle["ilr"].answers) if bundle["ilr"] else {}
    rows = []

    kin = _d(answers.get("nextOfKin"))
    if kin.get("fullName"):
        rows.append({
            "id": "wiz-kin",
            "name": _s(kin.get("fullName")),
            "type": "Next of kin",
            "phone": _s(kin.get("phone")),
            "email": _s(kin.get("email")),
            "role": _s(kin.get("relationship")),
            "notes": "Same address as learner" if kin.get("sameAddressAsLearner") else "",
        })

    emp = _d(answers.get("employer"))
    if emp.get("lineManagerName"):
        org = _s(emp.get("organisationName"))
        rows.append({
            "id": "wiz-manager",
            "name": _s(emp.get("lineManagerName")),
            "type": "Line manager",
            "phone": _s(emp.get("lineManagerPhone")),
            "email": _s(emp.get("lineManagerEmail")),
            "role": f"Line manager at {org}" if org else "Line manager",
            "notes": ", ".join(p for p in (_s(emp.get("city")), _s(emp.get("postcode"))) if p),
        })
    return rows


def _tracker(bundle):
    """Onboarding completion as tracker rows.

    The tracker panel is a checklist of compliance artefacts, which is exactly
    what the wizard produces: a signed ILR, an acknowledged policy set, a CV
    and an eligibility evidence file.
    """
    ilr = bundle["ilr"]
    rows = []
    if ilr:
        answers = _d(ilr.answers)
        signed_bits = []
        if ilr.learner_signed:
            signed_bits.append(f"learner {_fmt_date(ilr.learner_signed_date)}".strip())
        if ilr.provider_signed:
            signed_bits.append(f"provider {_fmt_date(ilr.provider_signed_date)}".strip())
        rows.append({
            "id": "wiz-ilr",
            "type": "Extended ILR",
            "status": "Complete" if ilr.completed else "In progress",
            "programme": "",
            "description": "Signed by " + " and ".join(signed_bits) if signed_bits else "Not yet signed",
            "documents": "1",
        })

        evidence = _d(answers.get("eligibility")).get("evidenceFiles") or []
        if evidence:
            rows.append({
                "id": "wiz-eligibility",
                "type": "Eligibility evidence",
                "status": "Provided",
                "programme": "",
                "description": _s(_d(answers.get("eligibility")).get("evidenceDescription")) or "Right-to-work evidence",
                "documents": str(len(evidence)),
            })

    acks = bundle["policies"]
    if acks:
        done = sum(1 for a in acks if a.acknowledged)
        rows.append({
            "id": "wiz-policies",
            "type": "Policies",
            "status": "Complete" if done == len(acks) else "In progress",
            "programme": "",
            "description": f"{done} of {len(acks)} acknowledged",
            "documents": str(len(acks)),
        })

    cv = bundle["cv_job"]
    if cv and (cv.cv_file or cv.experience_text):
        rows.append({
            "id": "wiz-cv",
            "type": "CV / Job description",
            "status": "Provided",
            "programme": "",
            "description": _s(cv.pm_qualifications) or "Submitted during onboarding",
            "documents": "1" if cv.cv_file else "0",
        })

    # The Competencies panel only shows the weakest COMPETENCY_PANEL_LIMIT KSBs,
    # so state the real total here — otherwise a capped list reads as the whole
    # self-assessment.
    rated = [k for k in bundle["ksbs"] if k.level]
    if rated:
        shown = min(len(rated), COMPETENCY_PANEL_LIMIT)
        needs = sum(1 for k in rated if (k.score or 0) <= 3)
        rows.append({
            "id": "wiz-skills-radar",
            "type": "Skills radar",
            "status": "Complete",
            "programme": "",
            "description": (
                f"{len(rated)} KSBs self-assessed · {needs} rated 'occasionally' or lower · "
                f"Competencies panel lists the {shown} lowest"
            ),
            "documents": "0",
        })
    return rows


def _documents(bundle):
    """Files the learner actually uploaded through the wizard."""
    rows = []
    ilr = bundle["ilr"]
    if ilr:
        for i, name in enumerate(_d(_d(ilr.answers).get("eligibility")).get("evidenceFiles") or []):
            rows.append({
                "id": f"wiz-doc-ev-{i}",
                "uploaded": _fmt_date(ilr.updated_at),
                "description": f"Eligibility evidence — {_s(name)}",
                "fileName": _s(name),
            })
    cv = bundle["cv_job"]
    if cv and cv.cv_file:
        rows.append({
            "id": "wiz-doc-cv",
            "uploaded": _fmt_date(cv.updated_at),
            "description": "CV / job description",
            "fileName": _s(cv.cv_file),
        })
    return rows


def _activities(bundle):
    """A dated trail of what the learner did during onboarding.

    The Activities panel was empty for wizard learners even though every step
    is timestamped, so this reconstructs the onboarding history from those
    timestamps — newest first, matching how the panel reads.
    """
    rows = []
    personal = bundle["personal"]
    if personal:
        rows.append((personal.created_at, "Onboarding started", "Personal details saved"))
        if personal.signature:
            rows.append((personal.signature_date or personal.updated_at, "Signed", "Learner signature captured"))

    ksbs = bundle["ksbs"]
    if ksbs:
        latest = max(k.updated_at for k in ksbs if k.updated_at) if any(k.updated_at for k in ksbs) else None
        rows.append((latest, "Completed", f"Skills radar self-assessment ({len(ksbs)} KSBs rated)"))

    for ack in bundle["policies"]:
        if ack.acknowledged:
            rows.append((ack.acknowledged_at, "Acknowledged", f"Policy: {_policy_label(ack.policy_id)}"))

    cv = bundle["cv_job"]
    if cv:
        rows.append((cv.updated_at, "Uploaded", "CV / job description submitted"))

    ilr = bundle["ilr"]
    if ilr:
        if ilr.learner_signed:
            rows.append((ilr.learner_signed_date or ilr.updated_at, "Signed", "Extended ILR signed by learner"))
        if ilr.provider_signed:
            rows.append((ilr.provider_signed_date or ilr.updated_at, "Signed", "Extended ILR signed by provider"))
        if ilr.completed:
            rows.append((ilr.updated_at, "Completed", "Extended ILR submitted"))

    # Sort newest first. Timestamps are a mix of date/datetime/str/None, so key
    # on the formatted ISO-ish string rather than comparing mixed types.
    def sort_key(item):
        stamp = item[0]
        if stamp is None:
            return ""
        return stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp)

    rows.sort(key=sort_key, reverse=True)
    return [
        {"id": f"wiz-act-{i}", "date": _fmt_date(stamp), "timeAndStatus": status, "event": event}
        for i, (stamp, status, event) in enumerate(rows)
    ]


def _policy_label(policy_id):
    """'kbc-3' -> the third policy's name, falling back to the raw id."""
    text = _s(policy_id)
    _, _, idx = text.rpartition("-")
    if idx.isdigit() and int(idx) < len(POLICY_LABELS):
        return POLICY_LABELS[int(idx)]
    return text


# The skills radar rates every KSB on the standard, which for a full standard is
# 80+ rows — far more than the Competencies panel can usefully show. Only the
# lowest-rated ones are development needs, so the panel is capped at that end.
COMPETENCY_PANEL_LIMIT = 12


def _ksb_code(ksb_id):
    """'KSBP-20260610091738000012-s3' -> 'S3'; 'PCP-K1' -> 'K1'.

    The stored id is a catalogue key, not something to show a coach. The trailing
    segment is the KSB code from the standard, which is what the panel wants.
    curriculum.ksb_mappings holds full descriptions but covers only a fraction of
    these ids, so joining it would label some rows and not others.
    """
    text = _s(ksb_id)
    _, _, tail = text.rpartition("-")
    return tail.upper() if tail else text


def _competencies(bundle):
    """Skills-radar ratings as competency rows, weakest first.

    The panel lists a name and a version; the self-assessed level is the most
    useful thing to put in the version slot, and ordering by score puts the
    development needs at the top where a coach will see them.
    """
    ksbs = [k for k in bundle["ksbs"] if k.level]
    ksbs.sort(key=lambda k: (k.score if k.score is not None else 99, _s(k.ksb_id)))
    return [
        {"id": f"wiz-ksb-{k.id}", "name": f"{_ksb_code(k.ksb_id)} — self-assessed", "version": _s(k.level)}
        for k in ksbs[:COMPETENCY_PANEL_LIMIT]
    ]


def _aims(bundle):
    """Functional-skills enrolment and prior qualifications as aim rows."""
    rows = []
    cv = bundle["cv_job"]
    if cv and cv.functional_skills_enrol:
        rows.append({
            "aimRef": "FS",
            "qualification": f"Functional Skills — {_s(cv.functional_skills_enrol)}",
            "startDate": "",
            "endDate": "",
            "exempt": False,
        })
    for r in bundle["plr_records"]:
        rows.append({
            "aimRef": _s(r.record_ref),
            "qualification": " ".join(p for p in (_s(r.qualification_type), _s(r.subject)) if p) or _s(r.subject),
            "startDate": "",
            "endDate": _fmt_date(r.award_date),
            # A prior achievement is evidence toward exemption, not the aim itself.
            "exempt": True,
        })
    return rows


def _notes(bundle):
    """Free-text answers worth surfacing as notes.

    These are the questions where the learner wrote prose rather than picking
    an option, and they are the parts a coach actually needs to read.
    """
    ilr = bundle["ilr"]
    if not ilr:
        return []
    answers = _d(ilr.answers)
    stamp = _fmt_date(ilr.updated_at)
    author = _s(ilr.learner_name) or "Learner"

    understanding = _d(answers.get("understanding"))
    circumstances = _d(answers.get("circumstances"))
    cv = bundle["cv_job"]

    candidates = [
        ("Programme understanding", understanding.get("programmeUnderstanding")),
        ("Career progression", understanding.get("careerProgression")),
        ("Caring responsibilities", circumstances.get("caringResponsibilities")),
        ("Other circumstances", circumstances.get("other")),
        ("Relevant experience", cv.experience_text if cv else None),
        ("Project-management qualifications", cv.pm_qualifications if cv else None),
    ]
    return [
        {"id": f"wiz-note-{i}", "text": f"{label}: {_s(value)}", "administrator": author, "dateTime": stamp}
        for i, (label, value) in enumerate(candidates)
        if _s(value)
    ]


def _sub_programmes(bundle):
    """The onboarding phase as a sub-programme row, so the panel isn't blank."""
    ilr = bundle["ilr"]
    if not ilr:
        return []
    return [{
        "name": "Onboarding" + (" (complete)" if ilr.completed else " (in progress)"),
        "startDate": _fmt_date(ilr.created_at),
        "endDate": _fmt_date(ilr.updated_at) if ilr.completed else "",
    }]


def _managed_jobs(bundle):
    """The learner's employer, as the placement the programme is delivered at."""
    ilr = bundle["ilr"]
    if not ilr:
        return []
    emp = _d(_d(ilr.answers).get("employer"))
    org = _s(emp.get("organisationName"))
    if not org:
        return []
    where = ", ".join(p for p in (_s(emp.get("address")), _s(emp.get("city")), _s(emp.get("postcode"))) if p)
    return [{
        "id": "wiz-employer",
        "employer": org,
        "title": "Apprenticeship placement",
        "categories": "",
        "availableFrom": "",
        "availableTo": "",
        "hoursPlanned": "00:00",
        "hoursLogged": "00:00",
        "status": "Confirmed",
        "date": "",
        "comments": where,
        "canUnverify": False,
    }]


# --------------------------------------------------------------------------- #
# public entry point                                                          #
# --------------------------------------------------------------------------- #
def merge_wizard_sections(board, learner_id):
    """Fill empty board sections from the wizard tables, in place.

    Gap-filling only: a section that already has rows came from the delivery
    importers and is left untouched. Returns the same dict for convenience.
    """
    bundle = load_wizard_bundle(learner_id)
    if not bundle:
        return board
    # Nothing captured for this learner — a delivery-imported learner with no
    # wizard history hits this and the board is unchanged.
    if not any(bundle.values()):
        return board

    programme = _d(board.get("programme"))

    derived = {
        "contacts": _contacts(bundle),
        "tracker": _tracker(bundle),
        "documents": _documents(bundle),
        "activities": _activities(bundle),
        "competencies": _competencies(bundle),
        "aims": _aims(bundle),
        "notes": _notes(bundle),
        "subProgrammes": _sub_programmes(bundle),
        "managedJobs": _managed_jobs(bundle),
    }
    for key, rows in derived.items():
        if rows and not board.get(key):
            board[key] = rows

    # The signature is captured once during onboarding and is the same mark the
    # mandate panel asks for, so show it rather than "No signature".
    personal = bundle["personal"]
    contact = board.setdefault("contact", {})
    if personal:
        if personal.signature and not contact.get("signatureUrl"):
            contact["signatureUrl"] = personal.signature
        if not _s(contact.get("dob")):
            contact["dob"] = _fmt_date(personal.date_of_birth)

    ilr = bundle["ilr"]
    if ilr and ilr.completed and not _s(programme.get("onboardingCompletedAt")):
        programme["onboardingCompletedAt"] = _fmt_date(ilr.updated_at)

    return board
