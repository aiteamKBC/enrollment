"""Central deny-list for learners intentionally hidden from the OTJ Ledger.

Source records are retained in the upstream mirrors.  Filtering at the API
boundary keeps the named learners out of search, profiles, activities and
shared activity/attendance rosters without destructively changing source data.
"""


EXCLUDED_APTEM_IDS = frozenset({
    3687,  # Colleen Stewart
    4147,  # Wemimo Buwanhot
    4576,  # Freya Johnson
    6450,  # Amber Deacon
    6943,  # Joanna Furnival
    9115,  # Celine Ababio
})

EXCLUDED_LEARNER_NAMES = frozenset({
    "amber deacon",
    "celine ababio",
    "colleen stewart",
    "freya johnson",
    "jackson cyprian",
    "joanna furnival",
    "joseph bailey",
    "wemimo buwanhot",
})


def normalize_learner_name(value):
    """Return a comparison-safe name while preserving meaningful characters."""
    return " ".join(str(value or "").strip().casefold().split())


def is_excluded_learner(aptem_id=None, learner_name=None):
    """Whether an Aptem identity is excluded from all OTJ Ledger surfaces."""
    try:
        normalized_id = int(str(aptem_id).strip())
    except (TypeError, ValueError):
        normalized_id = None
    return (
        normalized_id in EXCLUDED_APTEM_IDS
        or normalize_learner_name(learner_name) in EXCLUDED_LEARNER_NAMES
    )
