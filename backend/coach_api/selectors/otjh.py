"""Learner/source programme window shared by Dashboard and Learner Profile."""

PROFILE_WINDOW_FIELDS = ("start_date", "end_date")
SOURCE_WINDOW_FIELDS = (
    "start_date", "end_date", "learner_start_date", "learner_end_date",
    "apprenticeship_end_date", "practical_period_end_date",
)


def learner_programme_window(profile, source):
    def first_value(*names):
        for owner in (source, profile):
            for name in names:
                value = getattr(owner, name, None)
                if value is not None and str(value).strip().casefold() not in {
                    "", "none", "null", "--", "n/a",
                }:
                    return value
        return None

    return (
        first_value("start_date", "learner_start_date"),
        first_value("end_date", "learner_end_date", "apprenticeship_end_date",
                    "practical_period_end_date"),
    )
