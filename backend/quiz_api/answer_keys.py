"""Shared, database-free checks for authored choice questions."""
import re


CHOICE_TYPES = {"single_choice", "multiple_choice", "true_false"}


def choice_label(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def choice_key_error(question_type, answers, *, strict_duplicates=False, require_key=True):
    if question_type not in CHOICE_TYPES:
        return None
    labels = [choice_label(answer.get("text")) for answer in answers]
    if any(not label for label in labels):
        return "Answer options must not be blank."
    if strict_duplicates and len(labels) != len(set(labels)):
        return "Answer options must have different text. Remove duplicate options."
    correct = {choice_label(answer.get("text")) for answer in answers if answer.get("isCorrect")}
    if require_key and not correct:
        return "Select a correct answer before learners take this quiz."
    if question_type in {"single_choice", "true_false"} and len(correct) > 1:
        return "Select exactly one correct answer for a single-choice or true/false question."
    return None


def quiz_submission_error(quiz, submitted):
    """Reject ungradeable keys and stale option IDs before any attempt is saved."""
    if not isinstance(submitted, dict):
        return "Quiz answers must be an object keyed by question id."
    if not quiz.get("questions"):
        return "This quiz has no questions available. Please contact your tutor."
    question_ids = {str(q["id"]) for q in quiz["questions"]}
    if set(submitted) - question_ids:
        return "This quiz has changed. Reload it before submitting a new attempt."
    for index, question in enumerate(quiz["questions"], 1):
        answers = question.get("answers", [])
        error = choice_key_error(question["type"], answers)
        if error or not answers:
            return f"Question {index} needs its answer key reviewed. Please contact your tutor; no attempt has been recorded."
        value = submitted.get(str(question["id"]))
        if question["type"] in CHOICE_TYPES | {"ordering"} and value is not None:
            values = value if isinstance(value, list) else [value]
            valid_ids = {str(a["id"]) for a in answers}
            if any(isinstance(item, (dict, list, bool)) or str(item) not in valid_ids for item in values):
                return "This quiz has changed or contains an invalid answer selection. Reload it before submitting a new attempt."
    return None


def quiz_publication_error(questions):
    """Validate a complete native quiz before making it available to learners."""
    active = [question for question in questions if not question.get("isArchived")]
    if not active:
        return "Add at least one active question before publishing this quiz."
    for index, question in enumerate(active, 1):
        if question.get("type") not in CHOICE_TYPES | {"matching", "image_matching", "ordering", "keywords", "fill_gap"}:
            return f"Question {index}: Choose a supported question type before publishing."
        answers = question.get("answers", [])
        if not answers:
            return f"Question {index}: Add answer options before publishing this quiz."
        error = choice_key_error(question.get("type"), answers, strict_duplicates=True)
        if error:
            return f"Question {index}: {error}"
    return None
