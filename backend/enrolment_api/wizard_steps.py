"""Fan the wizard draft out into the per-step tables, and read it back.

The draft document (ExtendedIlr.wizard_draft) stays the source of truth for
"reopen exactly what was typed". These tables are the queryable projection of it,
so reporting doesn't have to dig through jsonb — see apply_enrolment_wizard_tables
for the rationale.

Writes are last-write-wins per learner and run inside the caller's transaction:
the projection is rebuilt from the incoming draft, so it can never disagree with
the document it came from. Child collections (KSBs, PLR records, policy acks) are
upserted by their natural key and rows absent from the payload are deleted, so
removing a PLR record in the UI removes it here too.
"""
import logging

from .models import (
    WizardCvJob,
    WizardIlrLearnerDetails,
    WizardKsbAssessment,
    WizardPersonalDetails,
    WizardPlr,
    WizardPlrRecord,
    WizardPolicyAck,
    WizardSkillsRadar,
)

logger = logging.getLogger(__name__)

# The 9-point self-assessment scale (see COMPETENCE_LEVELS in the frontend), plus
# the three legacy 5-point values so assessments saved before the scale was
# widened still round-trip instead of being silently dropped.
RAG_LEVELS = {
    "champion",
    "mastery",
    "expert",
    "proficient",
    "consistently",
    "frequently",
    "occasionally",
    "rarely",
    "never",
    # legacy
    "always",
    "often",
    "sometimes",
}

# Numeric score per level, so the DB can be queried/reported on without the
# client having to send it. Legacy values map onto the nearest new score.
LEVEL_SCORES = {
    "champion": 9, "mastery": 8, "expert": 7, "proficient": 6, "consistently": 5,
    "frequently": 4, "occasionally": 3, "rarely": 2, "never": 1,
    "always": 8, "often": 5, "sometimes": 3,
}


def _s(value):
    """Trimmed string, or None for blank — keeps '' out of nullable text columns."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date(value):
    """'YYYY-MM-DD' or None. Anything unparseable becomes None rather than raising:
    a malformed optional date must not fail the learner's whole save."""
    text = _s(value)
    if not text:
        return None
    # Django accepts an ISO string for a DateField; reject anything else early so
    # the error surfaces here rather than as a DB-level cast failure.
    parts = text.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return text
    return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value):
    """True/False, or None for unanswered — a Yes/No the learner skipped."""
    return value if isinstance(value, bool) else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def project_draft(kind, learner_id, draft):
    """Write `draft` into the per-step tables for one learner.

    Absent sections are skipped, not cleared: a client that only sends
    personalDetails must not wipe the learner's PLR records.
    """
    draft = _dict(draft)
    scope = {"learner_kind": kind, "learner_id": learner_id}

    if "personalDetails" in draft:
        pd = _dict(draft["personalDetails"])
        sig = _s(pd.get("signature"))
        WizardPersonalDetails.objects.update_or_create(
            **scope,
            defaults={
                "first_name": _s(pd.get("firstName")),
                "last_name": _s(pd.get("lastName")),
                "email": _s(pd.get("email")),
                "phone": _s(pd.get("phone")),
                "address": _s(pd.get("address")),
                "date_of_birth": _date(pd.get("dob")),
                "age": _int(pd.get("age")),
                "sex": _s(pd.get("sex")),
                "signature": sig,
                # Stamp the signing date when one wasn't supplied but a signature was.
                "signature_date": _date(pd.get("signatureDate")),
            },
        )

    if ILR_DETAILS_KEY in draft:
        d = _dict(draft[ILR_DETAILS_KEY])
        WizardIlrLearnerDetails.objects.update_or_create(
            **scope,
            defaults={
                "years_at_address": _int(d.get("yearsAtAddress")),
                "at_address_since_birth": _bool(d.get("sinceBirth")),
                "postcode_prior_to_enrolment": _s(d.get("postcodePriorToEnrolment")),
                "national_insurance_number": _s(d.get("niNumber")),
                "ni_number_applied": _bool(d.get("niApplied")),
                "legal_sex": _s(d.get("legalSex")),
                "pronouns": _s(d.get("pronouns")),
                "ethnicity": _s(d.get("ethnicity")),
                "long_term_disability": _bool(d.get("longTermDisability")),
                "highest_qualification": _s(d.get("highestQualification")),
                "employment_status": _s(d.get("employmentStatus")),
                "employment_start_date": _date(d.get("employmentStartDate")),
                "job_title": _s(d.get("jobTitle")),
                "self_employed": _bool(d.get("selfEmployed")),
                "full_time_education": _bool(d.get("fullTimeEducation")),
                "expected_leaving_date": _date(d.get("expectedLeavingDate")),
                "length_of_unemployment": _s(d.get("lengthOfUnemployment")),
                "volunteers": _bool(d.get("volunteers")),
                "state_benefits": _s(d.get("stateBenefits")),
                "benefit_claim_basis": _s(d.get("benefitClaimBasis")),
                "declaration_signature": _s(d.get("signature")),
                "declaration_signed_date": _date(d.get("signatureDate")),
            },
        )

    if "skillsRadar" in draft:
        sr = _dict(draft["skillsRadar"])
        WizardSkillsRadar.objects.update_or_create(
            **scope, defaults={"standard_id": _s(sr.get("standardId"))}
        )

        assessments = _dict(sr.get("assessments"))
        seen = []
        for ksb_id, raw in assessments.items():
            a = _dict(raw)
            plan = _dict(a.get("actionPlan"))
            level = _s(a.get("level"))
            if level and level.lower() not in RAG_LEVELS:
                level = None  # ignore an unknown RAG value rather than storing junk
            WizardKsbAssessment.objects.update_or_create(
                **scope,
                ksb_id=str(ksb_id),
                defaults={
                    "level": level.lower() if level else None,
                    "score": LEVEL_SCORES.get(level.lower()) if level else None,
                    "note": _s(a.get("note")),
                    "action_text": _s(plan.get("text")),
                    "action": _s(plan.get("action")),
                    "goal": _s(plan.get("goal")),
                    "due_date": _date(plan.get("dueDate")),
                    "evidence_files": _list(a.get("evidenceFiles")),
                },
            )
            seen.append(str(ksb_id))
        # Drop assessments the learner has since cleared.
        WizardKsbAssessment.objects.filter(**scope).exclude(ksb_id__in=seen).delete()

    if "plr" in draft:
        plr = _dict(draft["plr"])
        WizardPlr.objects.update_or_create(**scope, defaults={"uln": _s(plr.get("uln"))})

        seen = []
        for i, raw in enumerate(_list(plr.get("records"))):
            r = _dict(raw)
            # Fall back to the index so a record with no client id still gets a
            # stable key instead of colliding on ''.
            ref = _s(r.get("id")) or f"idx-{i}"
            WizardPlrRecord.objects.update_or_create(
                **scope,
                record_ref=ref,
                defaults={
                    "place_of_study": _s(r.get("placeOfStudy")),
                    "qualification_type": _s(r.get("qualificationType")),
                    "subject": _s(r.get("subject")),
                    "level": _s(r.get("level")),
                    "award_date": _date(r.get("awardDate")),
                    "credits": _int(r.get("credits")),
                    "grade": _s(r.get("grade")),
                    "record_type": _s(r.get("recordType")),
                    "start_date": _date(r.get("startDate")),
                    "end_date": _date(r.get("endDate")),
                },
            )
            seen.append(ref)
        removed = WizardPlrRecord.objects.filter(**scope).exclude(record_ref__in=seen)
        # A removed record takes its certificate files with it — once the delete
        # has committed, so a rolled-back save never loses a file it kept.
        orphaned = [item for row in removed for item in (row.evidence or []) if isinstance(item, dict)]
        removed.delete()
        if orphaned:
            from django.db import transaction

            from .learner_uploads import remove_blob

            transaction.on_commit(lambda: [remove_blob(item) for item in orphaned], using="enrolment")

    if "cvJob" in draft:
        cv = _dict(draft["cvJob"])
        WizardCvJob.objects.update_or_create(
            **scope,
            defaults={
                "cv_file": _s(cv.get("cvFile")),
                "experience_text": _s(cv.get("experienceText")),
                "pm_qualifications": _s(cv.get("pmQualifications")),
                "functional_skills_enrol": _s(cv.get("functionalSkillsEnrol")),
                "highest_qualification": _s(cv.get("highestQualification")),
                "highest_qualification_field": _s(cv.get("highestQualificationField")),
                "has_field_qualification": _bool(cv.get("hasFieldQualification")),
                "highest_field_qualification": _s(cv.get("highestFieldQualification")),
                "gcse_english": _bool(cv.get("gcseEnglish")),
                "gcse_maths": _bool(cv.get("gcseMaths")),
            },
        )

    if "policies" in draft:
        from django.utils import timezone

        acknowledged = _dict(_dict(draft["policies"]).get("acknowledged"))
        seen = []
        for policy_id, value in acknowledged.items():
            is_ack = bool(value)
            row, _created = WizardPolicyAck.objects.update_or_create(
                **scope, policy_id=str(policy_id), defaults={"acknowledged": is_ack}
            )
            # Only stamp the first time it flips to acknowledged, so re-saving the
            # wizard doesn't keep moving the acknowledgement date.
            if is_ack and row.acknowledged_at is None:
                row.acknowledged_at = timezone.now()
                row.save(update_fields=["acknowledged_at"])
            elif not is_ack and row.acknowledged_at is not None:
                row.acknowledged_at = None
                row.save(update_fields=["acknowledged_at"])
            seen.append(str(policy_id))
        WizardPolicyAck.objects.filter(**scope).exclude(policy_id__in=seen).delete()


#: The ILR Learner Details step's key in the wizard draft. Stored ONLY in
#: enrolment."Wizard_Ilr_Learner_Details" — never in enrolment."Extended_ILR",
#: whose Wizard_draft snapshot has this key removed before it is written (see
#: extended_ilr.py). The ILR and the Extended ILR are separate records.
ILR_DETAILS_KEY = "ilrDetails"


def without_ilr_details(draft):
    """The draft as enrolment."Extended_ILR" may store it: no ILR Learner Details."""
    return {key: value for key, value in _dict(draft).items() if key != ILR_DETAILS_KEY}


def read_ilr_details(kind, learner_id):
    """This learner's ILR Learner Details in draft shape, or None if never saved."""
    row = WizardIlrLearnerDetails.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    return ilr_details_draft(row) if row else None


def ilr_details_draft(row):
    """One Wizard_Ilr_Learner_Details row in the draft's ilrDetails shape."""
    return {
        "yearsAtAddress": row.years_at_address,
        "sinceBirth": row.at_address_since_birth,
        "postcodePriorToEnrolment": row.postcode_prior_to_enrolment or "",
        "niNumber": row.national_insurance_number or "",
        "niApplied": row.ni_number_applied,
        "legalSex": row.legal_sex or "",
        "pronouns": row.pronouns or "",
        "ethnicity": row.ethnicity or "",
        "longTermDisability": row.long_term_disability,
        "highestQualification": row.highest_qualification or "",
        "employmentStatus": row.employment_status or "",
        "employmentStartDate": str(row.employment_start_date) if row.employment_start_date else "",
        "jobTitle": row.job_title or "",
        "selfEmployed": row.self_employed,
        "fullTimeEducation": row.full_time_education,
        "expectedLeavingDate": str(row.expected_leaving_date) if row.expected_leaving_date else "",
        "lengthOfUnemployment": row.length_of_unemployment or "",
        "volunteers": row.volunteers,
        "stateBenefits": row.state_benefits or "",
        "benefitClaimBasis": row.benefit_claim_basis or "",
        "signature": row.declaration_signature or None,
        "signatureDate": str(row.declaration_signed_date) if row.declaration_signed_date else "",
    }


def read_projection(kind, learner_id):
    """Rebuild the draft shape from the per-step tables.

    Used as a fallback for rows saved before the projection existed, and as the
    canonical read once these tables are the system of record.
    """
    scope = {"learner_kind": kind, "learner_id": learner_id}
    out = {}

    pd = WizardPersonalDetails.objects.filter(**scope).first()
    if pd:
        out["personalDetails"] = {
            "firstName": pd.first_name or "",
            "lastName": pd.last_name or "",
            "email": pd.email or "",
            "phone": pd.phone or "",
            "address": pd.address or "",
            "dob": str(pd.date_of_birth) if pd.date_of_birth else "",
            "age": pd.age,
            "sex": pd.sex or "",
            "signature": pd.signature or None,
            "signatureDate": str(pd.signature_date) if pd.signature_date else "",
        }

    ilr_details = WizardIlrLearnerDetails.objects.filter(**scope).first()
    if ilr_details:
        out[ILR_DETAILS_KEY] = ilr_details_draft(ilr_details)

    sr = WizardSkillsRadar.objects.filter(**scope).first()
    ksbs = list(WizardKsbAssessment.objects.filter(**scope))
    if sr or ksbs:
        out["skillsRadar"] = {
            "standardId": (sr.standard_id if sr else "") or "",
            "assessments": {
                k.ksb_id: {
                    "ksbId": k.ksb_id,
                    "level": k.level,
                    "evidenceFiles": k.evidence_files or [],
                    "note": k.note or "",
                    "actionPlan": (
                        {
                            "text": k.action_text or "",
                            "action": k.action or "",
                            "goal": k.goal or "",
                            "dueDate": str(k.due_date) if k.due_date else "",
                        }
                        if (k.action_text or k.action or k.goal or k.due_date)
                        else None
                    ),
                }
                for k in ksbs
            },
        }

    plr = WizardPlr.objects.filter(**scope).first()
    records = list(WizardPlrRecord.objects.filter(**scope).order_by("id"))
    if plr or records:
        out["plr"] = {
            "uln": (plr.uln if plr else "") or "",
            "records": [
                {
                    "id": r.record_ref,
                    "placeOfStudy": r.place_of_study or "",
                    "qualificationType": r.qualification_type or "",
                    "subject": r.subject or "",
                    "level": r.level or "",
                    "awardDate": str(r.award_date) if r.award_date else "",
                    "credits": r.credits or 0,
                    "grade": r.grade or "",
                    "recordType": r.record_type or "",
                    "startDate": str(r.start_date) if r.start_date else "",
                    "endDate": str(r.end_date) if r.end_date else "",
                }
                for r in records
            ],
        }

    cv = WizardCvJob.objects.filter(**scope).first()
    if cv:
        out["cvJob"] = {
            "cvFile": cv.cv_file or "",
            "experienceText": cv.experience_text or "",
            "pmQualifications": cv.pm_qualifications or "",
            "functionalSkillsEnrol": cv.functional_skills_enrol or "",
            "highestQualification": cv.highest_qualification or "",
            "highestQualificationField": cv.highest_qualification_field or "",
            "hasFieldQualification": cv.has_field_qualification,
            "highestFieldQualification": cv.highest_field_qualification or "",
            "gcseEnglish": cv.gcse_english,
            "gcseMaths": cv.gcse_maths,
        }

    acks = list(WizardPolicyAck.objects.filter(**scope))
    if acks:
        out["policies"] = {"acknowledged": {a.policy_id: a.acknowledged for a in acks}}

    return out
