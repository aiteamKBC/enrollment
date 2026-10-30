"""Reuse source identity only after the Case File's coach ownership gate."""


def source_for_case_file(request, kind, enrolment_id):
    context = getattr(request, '_case_file_context', None)
    if context is None:
        return None
    # This attribute is installed by server code, never accepted in a payload.
    if context.coach != getattr(request, 'coach_email', None):
        return None
    if context.profile is None or str(context.profile.enrolment_id) != str(enrolment_id) or context.kind != kind:
        return None
    return context.source
