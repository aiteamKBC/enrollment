"""Template-owned signature policy for Aptem continuation reviews."""

ROLES = ("advisor", "participant", "employer", "referrer")
CONFIGURABLE_ROLES = ROLES[:3]
FAMILY_REQUIRED_ROLES = {
    "MCM": ("advisor", "participant"),
    "PR": ("advisor", "participant", "employer"),
    "PR_SKILLS_RADAR": ("advisor", "participant", "employer"),
    "PSP": (),
}


def requirements_for_family(family):
    if family not in FAMILY_REQUIRED_ROLES:
        raise ValueError("Unsupported migrated review family.")
    return {role: role in FAMILY_REQUIRED_ROLES[family] for role in ROLES}


def validate_template_signatures(definition, family=None):
    if "signatures" not in definition:
        return
    rules = definition["signatures"]
    if (not isinstance(rules, dict) or set(rules) != set(CONFIGURABLE_ROLES)
            or any(type(value) is not bool for value in rules.values())):
        raise ValueError("Signatures must specify true or false for Coach, Learner and Employer only.")


def requirements_for_definition(definition, family):
    validate_template_signatures(definition, family)
    defaults = requirements_for_family(family)
    # Missing settings preserve the established defaults of existing templates.
    # An explicit all-false selection means completion without signatures.
    return {**defaults, **definition.get("signatures", {}), "referrer": False}
