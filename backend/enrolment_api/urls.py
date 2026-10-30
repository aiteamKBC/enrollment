from django.urls import path

from . import cv_job_documents, documents, eligibility_form, eligibility_layout, extended_ilr, ilr_eligibility_evidence, ilr_employer_details, ilr_learner_record, plr_evidence, policy_documents, postcode_lookup, views, wizard_bootstrap, wizard_custom_uploads, wizard_inclusion, wizard_layout

urlpatterns = [
    path('health/', views.health, name='enrolment-health'),
    path('commercial-users/<int:pk>/board/', views.commercial_board, name='commercial-board'),
    # Public apprenticeship eligibility form (/apprenticeship_application) — no sign-in
    path('public/apprenticeship-application/', eligibility_form.submit_application, name='public-apprenticeship-application'),
    # ...the form it shows (published from the eligibility form builder) and its postcode check — no sign-in
    path('public/eligibility-form-layout/', eligibility_layout.public_eligibility_form_layout, name='public-eligibility-form-layout'),
    path('public/postcode-lookup/', postcode_lookup.postcode_lookup, name='public-postcode-lookup'),
    # The eligibility form builder: the published layout and its publish (enrolment staff)
    path('eligibility-form-layout/', eligibility_layout.eligibility_form_layout, name='eligibility-form-layout'),
    path('eligibility-form-layout/publish/', eligibility_layout.publish_eligibility_form_layout, name='eligibility-form-layout-publish'),
    # Extended ILR questionnaire (kind: apprenticeship | commercial)
    path('extended-ilr/<str:kind>/<int:learner_id>/', extended_ilr.extended_ilr, name='extended-ilr'),
    # Eligibility proof of identification/residency (Azure-backed, path on the ILR row)
    path('extended-ilr/<str:kind>/<int:learner_id>/eligibility-evidence/', ilr_eligibility_evidence.eligibility_evidence, name='extended-ilr-eligibility-evidence'),
    path('extended-ilr/<str:kind>/<int:learner_id>/eligibility-evidence/<uuid:file_id>/', ilr_eligibility_evidence.delete_eligibility_evidence, name='extended-ilr-eligibility-evidence-delete'),
    path('extended-ilr/<str:kind>/<int:learner_id>/eligibility-evidence/<uuid:file_id>/download/', ilr_eligibility_evidence.download_eligibility_evidence, name='extended-ilr-eligibility-evidence-download'),
    # CV/Job Description step: CV, transcript and GCSE evidence (Azure-backed, path on the Wizard_Cv_Job row)
    path('wizard/<str:kind>/<int:learner_id>/cv-documents/', cv_job_documents.cv_documents, name='wizard-cv-documents'),
    path('wizard/<str:kind>/<int:learner_id>/cv-documents/<uuid:file_id>/', cv_job_documents.delete_cv_document, name='wizard-cv-document-delete'),
    path('wizard/<str:kind>/<int:learner_id>/cv-documents/<uuid:file_id>/download/', cv_job_documents.download_cv_document, name='wizard-cv-document-download'),
    # PLR entries' certificate/evidence files (Azure-backed, path on the Wizard_Plr_Records row)
    path('wizard/<str:kind>/<int:learner_id>/plr-evidence/', plr_evidence.plr_evidence, name='wizard-plr-evidence'),
    path('wizard/<str:kind>/<int:learner_id>/plr-evidence/<uuid:file_id>/', plr_evidence.delete_plr_evidence, name='wizard-plr-evidence-delete'),
    path('wizard/<str:kind>/<int:learner_id>/plr-evidence/<uuid:file_id>/download/', plr_evidence.download_plr_evidence, name='wizard-plr-evidence-download'),
    # The wizard builder: the published layout (read by every wizard) and its publish (enrolment staff)
    # The Inclusion step: whether the learner has answered the assessment on the Safeguarding site
    path('wizard/<str:kind>/<int:learner_id>/inclusion/', wizard_inclusion.wizard_inclusion, name='wizard-inclusion'),
    path('wizard-layout/', wizard_layout.wizard_layout, name='wizard-layout'),
    path('wizard-layout/publish/', wizard_layout.publish_wizard_layout, name='wizard-layout-publish'),
    # Files on the builder's custom upload fields (Azure-backed, path in the field's own column)
    path('wizard/<str:kind>/<int:learner_id>/custom-uploads/<str:field_key>/', wizard_custom_uploads.custom_uploads, name='wizard-custom-uploads'),
    path('wizard/<str:kind>/<int:learner_id>/custom-uploads/<str:field_key>/<uuid:file_id>/', wizard_custom_uploads.delete_custom_upload, name='wizard-custom-upload-delete'),
    path('wizard/<str:kind>/<int:learner_id>/custom-uploads/<str:field_key>/<uuid:file_id>/download/', wizard_custom_uploads.download_custom_upload, name='wizard-custom-upload-download'),
    # Employer Details prefill from the learner's employer and organisation (read-only)
    path('extended-ilr/<str:kind>/<int:learner_id>/employer-details/', ilr_employer_details.ilr_employer_details, name='extended-ilr-employer-details'),
    path('ilr-learner-record/<str:kind>/<int:learner_id>/', ilr_learner_record.ilr_learner_record, name='ilr-learner-record'),
    # Board + ILR together — one round-trip to open the wizard.
    path('wizard-bootstrap/<str:kind>/<int:learner_id>/', wizard_bootstrap.wizard_bootstrap, name='wizard-bootstrap'),
    # Generated compliance documents (Azure-backed)
    path('document-types/', documents.document_types, name='document-types'),
    path('policy-documents/<slug:doc_id>/', policy_documents.open_policy_document, name='policy-document-open'),
    path('documents/<str:kind>/<int:learner_id>/', documents.documents, name='enrolment-documents'),
    path('documents/<str:kind>/<int:learner_id>/<uuid:doc_id>/download/', documents.download_document, name='enrolment-document-download'),
    path('documents/<str:kind>/<int:learner_id>/<uuid:doc_id>/sign/', documents.sign_document, name='enrolment-document-sign'),
    # Replaces the stored PDF in place, so a document rebuilt with a new
    # signature keeps its id and the signatures already recorded on it.
    path('documents/<str:kind>/<int:learner_id>/<uuid:doc_id>/file/', documents.replace_document_file, name='enrolment-document-file'),
]
