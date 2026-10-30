"""Kent Business College policies the enrolment wizard asks learners to read.

The PDFs live in the private AZURE_ENROLMENT_DOCS_CONTAINER under POLICY_PREFIX,
put there by ``manage.py upload_policy_documents``. Opening one goes through
this view, which checks the caller is signed in and then redirects to a
short-lived read SAS — the container itself is never public.

    GET /enrolment_api/policy-documents/<doc_id>/   -> 302 to the PDF

The ids are what learners' acknowledgements are stored against
(enrolment."Wizard_Policy_Acks".Policy_id), so an id is never reused for a
different document: a replaced policy gets a new id, and the old tick stays in
the history for the document it was actually given to. The wizard's list
(frontend/src/mocks/enrolment-console.ts, POLICY_DOCS_KBC) must use these ids.
"""
from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_GET

from learner_api.evidence_storage import azure_configured, get_read_sas

from .auth import enrolment_login_required

#: Folder inside the enrolment documents container.
POLICY_PREFIX = "policies/kbc"

#: id -> file name, in the order the wizard lists them.
POLICY_DOCUMENTS = {
    "kbc-policy-attendance-engagement": "Kent Business College - Apprentice Attendance and Engagement Policy.pdf",
    "kbc-policy-british-values": "Kent Business College - British Values.pdf",
    "kbc-policy-business-continuity": "Kent Business College - Business Continuity Policy.pdf",
    "kbc-policy-complaint-procedures": "Kent Business College - Complaint Procedures Policy.pdf",
    "kbc-policy-equality-diversity-inclusion": "Kent Business College - Equality, Diversity and Inclusion Policy.pdf",
    "kbc-policy-harassment-bullying": "Kent Business College - Harassment and Bullying Policy.pdf",
    "kbc-policy-health-safety": "Kent Business College - Health and Safety.pdf",
    "kbc-policy-learner-code-of-conduct": "Kent Business College - Learner Code of Conduct.pdf",
    "kbc-policy-safeguarding-prevent": "Kent Business College - Safeguarding and Prevent Policy.pdf",
    "kbc-policy-safeguarding-prevent-handbook": "Safeguarding and Prevent Handbook Kent Business College.pdf",
    "kbc-policy-manager-handbook": "Manager_Handbook Kent Business College.pdf",
    "kbc-policy-library": "A1_-_Library_Policy.pdf",
    "kbc-policy-events-management": "A2_-_Events_Management_Policy.pdf",
    "kbc-policy-events-travel-reimbursement": "C4_Reimbursement_for_Events_Travel_and_Mileage_Claims_Policy.pdf",
    "kbc-policy-events-travel-short-guide": "C4a - Travel_Reimbursement_for_KBC_Events_Short_Guide (updated).pdf",
}


def blob_name_for(file_name):
    return f"{POLICY_PREFIX}/{file_name}"


def _inline_disposition(file_name):
    # Opens in the browser's PDF viewer rather than downloading. The name is
    # ours, but quotes and line breaks are stripped all the same, since it is
    # signed into a response header.
    safe = file_name.replace('"', "").replace("\r", "").replace("\n", "")
    return f'inline; filename="{safe}"'


@enrolment_login_required
@require_GET
def open_policy_document(request, doc_id):
    file_name = POLICY_DOCUMENTS.get(doc_id)
    if file_name is None:
        return JsonResponse({"error": "Policy document not found."}, status=404)
    if not azure_configured():
        return JsonResponse({"error": "Document storage is not configured."}, status=503)
    url = get_read_sas(
        settings.AZURE_ENROLMENT_DOCS_CONTAINER,
        blob_name_for(file_name),
        content_disposition=_inline_disposition(file_name),
    )
    return HttpResponseRedirect(url)
