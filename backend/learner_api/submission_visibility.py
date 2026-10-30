"""Keep generated activity queue entries from masking saved topic work."""

ACTIVITY_MARKING_REFLECTION = (
    "Submitted for marking from the activity. No written "
    "reflection was requested for this component."
)

# Queries using this predicate alias their outer row as `submission`. Only the
# known, unreviewed activity placeholder is suppressed, and only when the same
# learner/component has a saved topic document. Genuine legacy submissions and
# evidence-only activities remain visible. No historical rows are rewritten.
VISIBLE_SUBMISSION_SQL = f"""NOT (
    submission.activity_type = 'assignment'
    AND submission.assignment_topic_id = ''
    AND submission.status = 'submitted_for_tutor_review'
    AND COALESCE(submission.full_submission, '{{}}'::jsonb) = '{{}}'::jsonb
    AND COALESCE(submission.learning_reflection, '') = '{ACTIVITY_MARKING_REFLECTION}'
    AND COALESCE(submission.coach_feedback, '') = ''
    AND COALESCE(submission.reviewed_by, '') = ''
    AND submission.reviewed_at IS NULL
    AND EXISTS (
        SELECT 1 FROM "Learner".learning_reflection_submissions AS topic
        WHERE topic.learner_kind = submission.learner_kind
          AND topic.learner_id = submission.learner_id
          AND topic.activity_type = submission.activity_type
          AND topic.activity_id = submission.activity_id
          AND topic.assignment_topic_id <> ''
          AND COALESCE(topic.full_submission, '{{}}'::jsonb) <> '{{}}'::jsonb
    )
)"""
