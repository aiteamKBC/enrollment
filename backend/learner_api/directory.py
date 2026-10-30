"""Small, read-only learner projection for the Users directory."""
from django.db.models import BooleanField, Case, Q, Value, When

from .models import EnrolmentUser
from .aptem_status import with_aptem_status


# Keep this projection aligned with mappers.to_list_row. Plans and wizard JSON
# can be many megabytes; the directory needs only a boolean for its Add/Edit
# action, not the full documents or a lifecycle sweep for every learner.
DIRECTORY_FIELDS = (
    "id", "uuid", "username", "type", "email", "group", "status",
    "programme_status", "programme", "cohort", "organization", "learner_type",
    # The directory's Case owner filter offers the owners actually present in
    # these rows, so the column has to come back with them.
    "case_owner",
)


def _has_plan(field):
    # Match bool() in to_list_row for all JSON types, including SQL NULL and
    # JSON null. Cast/transfer neither plan to Python just to check its presence.
    return (
        Q(**{f"{field}__isnull": False})
        & ~Q(**{field: None})
        & ~Q(**{f"{field}__in": [[], {}, "", False, 0]})
    )


def learner_directory_queryset(learner_type=""):
    queryset = EnrolmentUser.all_learners.only(*DIRECTORY_FIELDS).annotate(
        _directory_has_learning_plan=Case(
            When(_has_plan("learning_plan") | _has_plan("training_plan"), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )
    if learner_type == "commercial":
        queryset = queryset.filter(learner_type="commercial")
    elif learner_type == "apprenticeship":
        # Rows predating the merge have a NULL type and are apprenticeship.
        queryset = queryset.exclude(learner_type="commercial")
    return with_aptem_status(queryset).order_by("id")
