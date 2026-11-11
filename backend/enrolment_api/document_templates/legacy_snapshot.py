"""Read-only compatibility for statutory snapshots predating stored PDFs."""

SNAPSHOT_NOTICE = (
    'Historical snapshot preview: rendered from the saved document data, template and recorded signatures '
    'using the saved document design. No matching saved PDF is available for this snapshot, so this '
    'is not a byte-for-byte copy of the original file. No document or signature has been changed.'
)

# Reviews can include live questionnaire/profile context and must keep their
# existing missing-history protection. Only the four frozen statutory sources
# have this compatibility path; revised copies are checked separately by callers.
SECTIONS = {
    'apprenticeship-agreement': {'particulars': dict, 'planModules': list},
    'ilr': {'learnerDetails': dict, 'answers': dict},
    'training-plan': {'programme': dict, 'employment': dict, 'learningPlan': list,
                      'otjh': dict, 'epa': dict, 'contacts': dict},
    'written-agreement': {'particulars': dict, 'delivery': dict, 'epa': dict,
                          'costs': dict, 'contacts': dict},
}


def can_render_original_snapshot(key, data):
    sections = SECTIONS.get(key)
    if sections is None or not isinstance(data, dict):
        return False
    template = data.get('template')
    if (not isinstance(template, dict) or not isinstance(template.get('content'), dict)
            or template.get('presentationTheme') or template.get('originalPdfVersion') == 2
            or data.get('documentPresentationTheme')):
        # A missing server-owned/branded artifact is an integrity/storage error,
        # not permission to generate a substitute or change historical branding.
        return False
    if any(not isinstance(data.get(name), expected) for name, expected in sections.items()):
        return False
    from .registry import seed
    signatures, marks = data.get('signatures'), data.get('marks')
    if not isinstance(signatures, dict) or not isinstance(marks, dict):
        return False
    for party in seed(key)['signatureRoles']:
        state = signatures.get(party)
        if not isinstance(state, dict):
            return False
        if state.get('signed') and (not isinstance(marks.get(party), str) or not marks[party].strip()):
            return False
    return True


def can_render_revision_snapshot(revision, data):
    if not can_render_original_snapshot(revision.issue.document_key, data):
        return False
    if revision.state == 'original':
        return revision.number == 1
    if revision.state != 'finalized' or revision.number <= 1:
        return False
    if revision.signature_method == 'keep':
        # Retained marks are historical evidence, never approval of revised text.
        return data.get('signatureProvenance') == revision.provenance and data.get('fullySigned') is False
    if revision.signature_method == 'sign-again' and revision.signing_source_id:
        from .revisions import source_record
        # Without the exact independent envelope, payload() would fall back to
        # inherited data. Those marks must never appear as fresh signatures.
        return source_record(revision.issue.document_key, revision.issue.learner_id,
                             revision.signing_source_id) is not None
    return False
