"""Verified identifier translation only: never infer identity from a name."""

CURRICULUM_PROGRAMME_IDS = {
    'ME_L4': 'PROG-ME-L4',
    'MM_L6': 'PROG-MM-L6',
    'APM_L4': 'PROG-20260907141619067932',
    'PCP_L6': 'PROG-PCP-L6',
}


def find_structure_programme(snapshot, identifier):
    """Accept canonical/curriculum IDs; ambiguous or conflicting bindings fail closed.

    The explicit map already exists in Admin's cohortBootstrap.ts and was checked
    in Phase 2. No saved IDs or published structures are rewritten by this adapter.
    """
    if not identifier:
        return None
    matches = []
    for programme in snapshot.get('programmes', []):
        canonical = programme.get('id')
        declared = programme.get('curriculumProgrammeId')
        verified = CURRICULUM_PROGRAMME_IDS.get(canonical)
        if declared and verified and declared != verified:
            # A changed binding needs explicit review, not a silent fallback.
            continue
        aliases = {canonical, declared or verified}
        if identifier in aliases:
            matches.append(programme)
    return matches[0] if len(matches) == 1 else None
