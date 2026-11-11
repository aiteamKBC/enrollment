from programme_suitability.test_settings import *  # noqa: F403

DOCUMENT_TEMPLATES_ENABLED = True
# Legacy regression fixtures deliberately retain their v1/v2 byte expectations.
# The branded runtime integration suite explicitly enables real Node rendering.
DOCUMENT_BRANDED_PDF_ENABLED = False
TEST_RUNNER = 'enrolment_api.document_templates.test_runner.DocumentTemplateRunner'
