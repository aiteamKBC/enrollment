"""Upload the Kent Business College policy PDFs the enrolment wizard links to.

Reads each file named in enrolment_api.policy_documents.POLICY_DOCUMENTS from a
local folder and writes it to AZURE_ENROLMENT_DOCS_CONTAINER under
policies/kbc/, replacing any earlier copy. Nothing else in the container is
touched, and a file in the folder that is not in the catalogue is reported, not
uploaded.

    python manage.py upload_policy_documents --dry-run   # show the plan only
    python manage.py upload_policy_documents             # upload
    python manage.py upload_policy_documents --source "<folder>"
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from enrolment_api.policy_documents import POLICY_DOCUMENTS, blob_name_for
from learner_api.evidence_storage import azure_configured, blob_exists, upload_blob

# <repo>/KBC enrolment policies — the folder is git-ignored; the files are
# distributed through storage, not the repository.
DEFAULT_SOURCE = Path(settings.BASE_DIR).parent / "KBC enrolment policies"


class Command(BaseCommand):
    help = "Upload the KBC policy PDFs linked from the enrolment wizard's Policies step."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Folder holding the PDFs.")
        parser.add_argument("--dry-run", action="store_true", help="List what would be uploaded, upload nothing.")

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.is_dir():
            raise CommandError(f"No such folder: {source}")

        wanted = set(POLICY_DOCUMENTS.values())
        present = {p.name for p in source.iterdir() if p.is_file()}
        missing = sorted(wanted - present)
        if missing:
            raise CommandError("Missing from the folder: " + "; ".join(missing))
        for extra in sorted(present - wanted):
            self.stdout.write(self.style.WARNING(f"Not in the catalogue, skipped: {extra}"))

        container = settings.AZURE_ENROLMENT_DOCS_CONTAINER
        # The account name identifies the target; it is not a credential.
        self.stdout.write(f"Target: account {settings.AZURE_STORAGE_ACCOUNT or '(unset)'}, container {container}")

        if options["dry_run"]:
            for file_name in POLICY_DOCUMENTS.values():
                self.stdout.write(f"  would upload {blob_name_for(file_name)} ({(source / file_name).stat().st_size} bytes)")
            self.stdout.write(self.style.WARNING("--dry-run: nothing uploaded."))
            return

        if not azure_configured():
            raise CommandError("Azure storage is not configured (AZURE_STORAGE_ACCOUNT / AZURE_STORAGE_KEY).")

        for file_name in POLICY_DOCUMENTS.values():
            blob = blob_name_for(file_name)
            with open(source / file_name, "rb") as fh:
                upload_blob(fh, container, blob, "application/pdf", overwrite=True)
            if not blob_exists(container, blob):
                raise CommandError(f"Uploaded but not found afterwards: {blob}")
            self.stdout.write(f"  uploaded {blob}")
        self.stdout.write(self.style.SUCCESS(f"{len(POLICY_DOCUMENTS)} policy documents uploaded and verified."))
