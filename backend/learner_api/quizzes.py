"""Quiz-taking API: fetch a quiz to take, and submit a graded attempt.

Data source: curriculum.quizzes -> curriculum.quiz_questions -> curriculum.quiz_answers
(the `curriculum` schema, same Neon DB as enrolment — read-only raw SQL, same
pattern as learner_api.curriculum).

    GET  /learner_api/quizzes/<int:quiz_id>/             -> quiz + questions + answers
                                                             (answers scrubbed of is_correct
                                                             for gradeable types, so the
                                                             correct answer isn't shipped
                                                             to the browser before grading)
    POST /learner_api/quizzes/<int:quiz_id>/submit/?learnerId=<id>&kind=<commercial|apprenticeship>
                                                          -> grades the attempt server-side
                                                             and appends it to
                                                             "Learner"."Active_users"."Weekly_Quizzes"

`matching`/`image_matching`/`ordering`/`keywords` answer rows are all authored
with is_correct=True (they ARE the answer key, not distractors), so those types
are graded structurally instead of by is_correct: matching/image_matching parse
each answer_text's "left -> right" pairing; ordering uses sort_order as the
correct sequence; keywords accepts any N of the listed answer_texts.
"""
import json
import logging
import random
import re

from django.db import DatabaseError, IntegrityError, connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .identity import learner_profile_for_source

from .active_users import ComponentReferenceError, save_progress_record
from .models import CommercialUser, EnrolmentUser
from .time_tracking import TrackingSessionError, tracking_session_already_used, verify_tracking_session
from .working_rules import (
    DeclaredCompletionError,
    resolve_completion_instants,
    validation_response_payload,
)
from login.permissions import learner_self_or_admin
from quiz_api.answer_keys import choice_label, quiz_submission_error

logger = logging.getLogger(__name__)

SOURCE_MODELS = {
    "commercial": CommercialUser,
    "apprenticeship": EnrolmentUser,
}

IMAGE_MATCHING_KIND = "image_matching_pair"
IMAGE_SOURCE_PATTERN = re.compile(r"\.(png|jpe?g|gif|webp|svg)(?:[?#].*)?$", flags=re.IGNORECASE)


def _conn():
    return connections["enrolment"]


def record_quiz_attempt(*, kind, learner_id, source, quiz, attempt_number, passed, grade_pct,
                        correct_count, question_count, time_taken, submitted_at, module_title, week_title):
    """One line in the Audit Trail for a quiz attempt. Never raises.

    The outcome only: which quiz, which attempt, the score and whether it
    passed. The answers stay in the learner's progress record, where marking
    reads them; the trail has no use for them.
    """
    try:
        from system_audit.writes import record_table_rows

        record_table_rows(
            'learner_quiz_attempts',
            [{
                'id': f'{kind}:{learner_id}:{quiz["id"]}:{attempt_number}',
                'learner_kind': kind,
                'learner_id': learner_id,
                'learner_name': getattr(source, 'username', '') or getattr(source, 'email', '') or learner_id,
                'quiz_id': quiz['id'],
                'quiz_title': quiz.get('title') or '',
                'module_title': module_title or quiz.get('module') or '',
                'week_title': week_title or '',
                'attempt': attempt_number,
                'result': 'Passed' if passed else 'Not passed',
                'score_percent': grade_pct,
                'correct_answers': correct_count,
                'total_questions': question_count,
                'time_taken': time_taken,
                'submitted_at': submitted_at,
                # The same instant twice: the attempt came into being now.
                'created_at': submitted_at,
                'updated_at': submitted_at,
            }],
            using='enrolment',
        )
    except Exception:
        logger.warning('Could not record a quiz attempt in the Audit Trail.', exc_info=True)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _format_clock(seconds):
    """Seconds -> "MM:SS" (e.g. 26 -> "00:26", 90 -> "01:30")."""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return None
    m, s = divmod(max(total, 0), 60)
    return f"{m:02d}:{s:02d}"


def _fetch_quiz(quiz_id):
    with _conn().cursor() as cur:
        cur.execute(
            "SELECT id, title, module, programme, week_id, duration, time_unit, "
            "passing_grade, randomize_questions, randomize_answers, default_question_type "
            "FROM curriculum.quizzes WHERE id = %s",
            [quiz_id],
        )
        row = cur.fetchone()
        if row is None:
            return None
        quiz = {
            "id": row[0], "title": row[1], "module": row[2], "programme": row[3],
            "weekId": row[4], "duration": row[5], "timeUnit": row[6],
            "passingGrade": row[7], "randomizeQuestions": row[8], "randomizeAnswers": row[9],
        }

        cur.execute(
            "SELECT id, question_text, question_type, points, sort_order, explanation, image_url "
            "FROM curriculum.quiz_questions WHERE quiz_id = %s AND is_archived = false "
            "ORDER BY sort_order",
            [quiz_id],
        )
        questions = [
            {
                "id": qid, "text": text, "type": qtype, "points": points,
                "sortOrder": sort_order, "explanation": explanation,
                "imageUrl": image_url or "",
            }
            for qid, text, qtype, points, sort_order, explanation, image_url in cur.fetchall()
        ]
        if not questions:
            quiz["questions"] = []
            return quiz

        q_ids = [q["id"] for q in questions]
        cur.execute(
            "SELECT id, question_id, answer_text, is_correct, sort_order "
            "FROM curriculum.quiz_answers WHERE question_id = ANY(%s) ORDER BY question_id, sort_order",
            [q_ids],
        )
        answers_by_question = {}
        for aid, qid, text, is_correct, sort_order in cur.fetchall():
            answers_by_question.setdefault(qid, []).append({
                "id": aid, "text": text, "isCorrect": is_correct, "sortOrder": sort_order,
            })

        for q in questions:
            q["answers"] = answers_by_question.get(q["id"], [])
        quiz["questions"] = questions
        return quiz


def _scrub_answer_key(quiz):
    """Build a client-safe copy of the quiz: every field that would reveal the
    correct choice before grading is stripped, and answers are shuffled so
    array position can't leak it either (matching/ordering's "correct" shape
    IS the authored row order, so shipping rows as-is would hand the answer
    to anyone who reads the array top-to-bottom)."""
    safe_questions = []
    for q in quiz["questions"]:
        qtype = q["type"]
        answers = q["answers"]
        safe_q = {k: v for k, v in q.items() if k != "answers"}

        if qtype in ("single_choice", "multiple_choice", "true_false"):
            # Legacy imports can contain indistinguishable options. Show one
            # per label; the grader accepts any ID belonging to that label.
            shuffled = list({choice_label(a["text"]): a for a in reversed(answers)}.values())
            random.shuffle(shuffled)
            safe_q["answers"] = [{"id": a["id"], "text": a["text"]} for a in shuffled]

        elif qtype == "fill_gap":
            safe_q["answers"] = []  # free-text entry; nothing to display

        elif qtype in ("matching", "image_matching"):
            lefts, rights = [], []
            for index, a in enumerate(answers):
                if qtype == "image_matching":
                    parsed = _parse_image_matching_answer(a["text"])
                    label = _pair_display_label(parsed, index)
                    left_item = {
                        "id": a["id"],
                        "left": label,
                        "leftKey": str(a["id"]),
                        "label": label,
                    }
                    if parsed.get("imageUrl"):
                        left_item["imageUrl"] = parsed["imageUrl"]
                    lefts.append(left_item)
                    rights.append(parsed["right"])
                else:
                    left, right = _split_pair_text(a["text"])
                    lefts.append({
                        "id": a["id"],
                        "left": left,
                        "leftKey": str(a["id"]),
                        "label": left,
                    })
                    rights.append(right)
            random.shuffle(lefts)
            random.shuffle(rights)
            safe_q["answers"] = lefts
            safe_q["rightOptions"] = rights

        elif qtype == "ordering":
            shuffled = list(answers)
            random.shuffle(shuffled)
            safe_q["answers"] = [{"id": a["id"], "text": a["text"]} for a in shuffled]

        elif qtype == "keywords":
            safe_q["answers"] = []  # free-text entry; nothing to display
            safe_q["answerCount"] = len(answers)

        else:
            safe_q["answers"] = [{"id": a["id"], "text": a["text"]} for a in answers]

        safe_questions.append(safe_q)

    return {
        **{k: v for k, v in quiz.items() if k != "questions"},
        "questions": safe_questions,
        "unavailableReason": quiz_submission_error(quiz, {}),
    }


@csrf_exempt
def quiz_detail(request, quiz_id):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        quiz = _fetch_quiz(quiz_id)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if quiz is None:
        return _error("Quiz not found.", 404)
    return JsonResponse(_scrub_answer_key(quiz))


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _split_pair_text(value):
    parts = re.split(r"\s*(?:->|=>|=)\s*", str(value or ""), maxsplit=1)
    left = parts[0].strip() if parts else ""
    right = parts[1].strip() if len(parts) > 1 else ""
    return left, right


def _looks_like_image_source(value):
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("data:image/"):
        return True
    return text.lower().startswith(("http://", "https://")) and bool(IMAGE_SOURCE_PATTERN.search(text))


def _parse_image_matching_answer(value):
    text = str(value or "").strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            image_url = str(payload.get("imageUrl") or "").strip()
            label = str(payload.get("label") or payload.get("left") or "").strip()
            match = str(payload.get("match") or payload.get("right") or "").strip()
            kind = str(payload.get("kind") or "").strip()
            if (image_url or label or match) and (not kind or kind == IMAGE_MATCHING_KIND):
                return {
                    "left": label,
                    "right": match,
                    "imageUrl": image_url,
                }

    left, right = _split_pair_text(text)
    image_url = left if _looks_like_image_source(left) else ""
    return {
        "left": "" if image_url else left,
        "right": right,
        "imageUrl": image_url,
    }


def _pair_display_label(parsed_pair, index):
    label = str(parsed_pair.get("left") or "").strip()
    if parsed_pair.get("imageUrl"):
        return label or f"Image {chr(65 + index)}"
    return label or f"Item {index + 1}"


def _answer_text_by_id(answers):
    return {a["id"]: a["text"] for a in answers}


def _grade_question(question, submitted):
    """Return a dict describing how one question was graded: points earned/
    possible, whether it was fully correct, and human-readable text for what
    the learner chose vs. what the correct answer actually was (so a stored
    attempt can show "you answered X, the correct answer was Y" without a
    second lookup against curriculum.quiz_answers)."""
    points = question["points"] or 1
    qtype = question["type"]
    answers = question["answers"]
    by_id = _answer_text_by_id(answers)

    if qtype in ("single_choice", "true_false"):
        correct_ids = {a["id"] for a in answers if a["isCorrect"]}
        chosen_id = _coerce_id(submitted) if isinstance(submitted, (int, str)) else None
        correct_labels = {choice_label(by_id[i]) for i in correct_ids}
        ok = chosen_id in by_id and choice_label(by_id[chosen_id]) in correct_labels
        return {
            "earned": points if ok else 0, "possible": points, "correct": ok,
            "chosenAnswer": by_id.get(chosen_id) if chosen_id is not None else None,
            "correctAnswer": ", ".join(dict.fromkeys(by_id[i] for i in sorted(correct_ids))),
            # ID form (for the slim stored record); resolved back to text on display.
            "chosenAnswerId": chosen_id,
            "correctAnswerId": sorted(correct_ids),
        }

    if qtype == "multiple_choice":
        correct_ids = {a["id"] for a in answers if a["isCorrect"]}
        submitted_ids = {_coerce_id(v) for v in submitted} if isinstance(submitted, list) else set()
        chosen_labels = {choice_label(by_id[i]) for i in submitted_ids if i in by_id}
        correct_labels = {choice_label(by_id[i]) for i in correct_ids}
        ok = submitted_ids.issubset(by_id) and chosen_labels == correct_labels and bool(correct_labels)
        return {
            "earned": points if ok else 0, "possible": points, "correct": ok,
            "chosenAnswer": ", ".join(dict.fromkeys(by_id[i] for i in by_id if i in submitted_ids)) or None,
            "correctAnswer": ", ".join(dict.fromkeys(by_id[i] for i in by_id if i in correct_ids)),
            "chosenAnswerId": sorted(submitted_ids),
            "correctAnswerId": sorted(correct_ids),
        }

    if qtype == "fill_gap":
        accepted = {_norm(a["text"]) for a in answers}
        ok = isinstance(submitted, str) and _norm(submitted) in accepted
        return {
            "earned": points if ok else 0, "possible": points, "correct": ok,
            "chosenAnswer": submitted if isinstance(submitted, str) and submitted else None,
            "correctAnswer": " / ".join(a["text"] for a in answers),
        }

    if qtype in ("matching", "image_matching"):
        pairs = []
        for index, a in enumerate(answers):
            if qtype == "image_matching":
                parsed = _parse_image_matching_answer(a["text"])
                legacy_key = parsed["left"] or parsed["imageUrl"]
            else:
                left, right = _split_pair_text(a["text"])
                parsed = {"left": left, "right": right, "imageUrl": ""}
                legacy_key = left

            pairs.append({
                "id": a["id"],
                "displayLeft": _pair_display_label(parsed, index),
                "legacyKey": str(legacy_key or "").strip(),
                "right": parsed["right"],
            })

        submitted_map = submitted if isinstance(submitted, dict) else {}

        def submitted_value(pair):
            for key in (str(pair["id"]), pair["legacyKey"], pair["displayLeft"]):
                if key and key in submitted_map:
                    return submitted_map.get(key) or ""
            return ""

        total = len(pairs) or 1
        matched = sum(1 for pair in pairs if _norm(submitted_value(pair)) == _norm(pair["right"]))
        ok = matched == total
        earned = round(points * matched / total, 2)
        chosen_pairs = "; ".join(f"{pair['displayLeft']} -> {submitted_value(pair) or '(no answer)'}" for pair in pairs)
        correct_pairs = "; ".join(f"{pair['displayLeft']} -> {pair['right']}" for pair in pairs)
        return {
            "earned": earned, "possible": points, "correct": ok,
            "chosenAnswer": chosen_pairs or None, "correctAnswer": correct_pairs,
        }

    if qtype == "ordering":
        correct_order = [a["id"] for a in sorted(answers, key=lambda a: a["sortOrder"])]
        submitted_order = [_coerce_id(v) for v in submitted] if isinstance(submitted, list) else []
        ok = (
            len(submitted_order) == len(correct_order)
            and len(set(submitted_order)) == len(correct_order)
            and all(i in by_id for i in submitted_order)
            and [choice_label(by_id[i]) for i in submitted_order] == [choice_label(by_id[i]) for i in correct_order]
        )
        return {
            "earned": points if ok else 0, "possible": points, "correct": ok,
            "chosenAnswer": " -> ".join(by_id[i] for i in submitted_order if i in by_id) or None,
            "correctAnswer": " -> ".join(by_id[i] for i in correct_order),
            "chosenAnswerId": submitted_order,
            "correctAnswerId": correct_order,
        }

    if qtype == "keywords":
        accepted = {_norm(a["text"]) for a in answers}
        submitted_words = [str(v) for v in submitted] if isinstance(submitted, list) else []
        submitted_norm = {_norm(v) for v in submitted_words}
        total = len(accepted) or 1
        matched = len(accepted & submitted_norm)
        ok = matched == total
        earned = round(points * matched / total, 2)
        return {
            "earned": earned, "possible": points, "correct": ok,
            "chosenAnswer": ", ".join(w for w in submitted_words if w.strip()) or None,
            "correctAnswer": ", ".join(a["text"] for a in answers),
        }

    return {"earned": 0, "possible": points, "correct": False, "chosenAnswer": None, "correctAnswer": None}


def _coerce_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


@csrf_exempt
# Owners and admins can submit attempts; admin actions retain their real actor.
@learner_self_or_admin(query_param="learnerId")
def submit_quiz_attempt(request, quiz_id):
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    kind = (request.GET.get("kind") or "").strip()
    learner_id = (request.GET.get("learnerId") or "").strip()
    model = SOURCE_MODELS.get(kind)
    if model is None or not learner_id:
        return _error("kind and learnerId query params are required.", 400)

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError) as exc:
        return _error(f"Invalid JSON body: {exc}", 400)

    # Working rules are decided here, before the quiz is fetched, graded or
    # persisted: a refusal must leave no attempt, no progress record, no audit
    # line and no OTJH credit behind. The learner's answers stay in the browser
    # and are re-posted unchanged with a declared instant, so the correction
    # never costs them a retake and grades to exactly the same score.
    from .retake_timing import choice
    try:
        retake_choice = choice(payload.get('retakeTimeChoice'))
    except ValueError as exc:
        return _error(str(exc), 400)
    submitted_at_dt = timezone.now()
    try:
        declared_at, validation_reason = resolve_completion_instants(
            payload, submitted_at_dt, quiz_id=quiz_id,
        )
    except DeclaredCompletionError as exc:
        return JsonResponse(validation_response_payload(exc), status=409)
    except DatabaseError:
        return _error("Could not verify the working-hours holiday calendar. Please try again.", 503)

    submitted_answers = payload.get("answers") or {}
    time_taken_seconds = payload.get("timeTakenSeconds")
    week_title = payload.get("week")
    module_title = payload.get("module")
    # Post-quiz reflection window: KSBs the learner marks this quiz as fulfilling,
    # a general feedback note, and their self-reported time-to-complete.
    ksbs = payload.get("ksbs") if isinstance(payload.get("ksbs"), list) else []
    feedback = payload.get("feedback") or ""
    reported_time = payload.get("reportedTime") or ""
    reflection_skipped = payload.get("skipReflection") is True

    try:
        quiz = _fetch_quiz(quiz_id)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if quiz is None:
        return _error("Quiz not found.", 404)

    grading_error = quiz_submission_error(quiz, submitted_answers)
    if grading_error:
        return _error(grading_error, 409)

    earned = 0.0
    possible = 0.0
    correct_count = 0
    breakdown = []       # full text, for the fresh submit response (results screen)
    stored_questions = []  # slim, id-only, for the persisted record
    FREE_TEXT = ("fill_gap", "keywords", "matching", "image_matching")
    for q in quiz["questions"]:
        submitted = submitted_answers.get(str(q["id"]))
        result = _grade_question(q, submitted)
        earned += result["earned"]
        possible += result["possible"]
        if result["correct"]:
            correct_count += 1
        breakdown.append({
            "questionId": q["id"],
            "questionText": q["text"],
            "imageUrl": q.get("imageUrl", ""),
            "type": q["type"],
            "points": q["points"],
            "earned": result["earned"],
            "possible": result["possible"],
            "correct": result["correct"],
            "chosenAnswer": result["chosenAnswer"],
            "correctAnswer": result["correctAnswer"],
        })
        # Slim record: reference answers by id; free-text types have no answer id,
        # so keep their (short) submitted/accepted text as a fallback.
        slim = {
            "questionId": q["id"],
            "questionText": q["text"],
            "type": q["type"],
            "possible": result["possible"],
            "chosenText": result["chosenAnswer"],
            "correctText": result["correctAnswer"],
            # Keep the diagram seen during this attempt, including no diagram.
            "imageUrl": q.get("imageUrl", ""),
            "earned": result["earned"],
            "correct": result["correct"],
        }
        if q["type"] in FREE_TEXT:
            slim["chosenText"] = result["chosenAnswer"]
        else:
            slim["chosenAnswerId"] = result.get("chosenAnswerId")
            slim["correctAnswerId"] = result.get("correctAnswerId")
        stored_questions.append(slim)

    question_count = len(quiz["questions"])
    grade_pct = round((earned / possible) * 100, 1) if possible else 0.0
    grade_decimal = round((earned / possible), 2) if possible else 0.0  # 0.90 form
    passed = grade_pct >= (quiz["passingGrade"] or 0)

    try:
        source = model.objects.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    try:
        active = learner_profile_for_source(source, learner_id, active_only=True)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    history = (active.training_plan_progress if active and isinstance(active.training_plan_progress, list) else [])
    # 1-based attempt number for THIS quiz — count prior quiz-kind records for it.
    # Normalised progress rows expose the CharField quiz_ref as text, whereas
    # curriculum quiz ids are integers. Canonicalise both sides so retakes are
    # numbered correctly after progress was moved out of the legacy JSON field.
    prior = sum(
        1
        for a in history
        if a.get("kind") == "quiz" and str(a.get("quizId")) == str(quiz["id"])
    )
    attempt_number = prior + 1

    try:
        tracking = verify_tracking_session(
            payload.get("trackingToken"),
            activity_kind="quiz",
            activity_id=quiz_id,
            learner_kind=kind,
            learner_id=learner_id,
            claimed_seconds=time_taken_seconds,
            submitted_at=submitted_at_dt,
        )
    except TrackingSessionError as exc:
        return _error(str(exc), 400)
    if tracking_session_already_used(tracking["sessionId"]):
        return _error("This activity timing session has already been submitted.", 409)
    started_at = tracking["startedAt"].isoformat()
    submitted_at = submitted_at_dt.isoformat()
    time_taken = _format_clock(tracking["verifiedSeconds"])

    # Slim, id-referenced record actually stored in Training_plan_progress.
    # Names (quizName/week/module) are dropped — the plan tree resolves them from
    # quizId; grade is a 0-1 decimal; score is split into achieved/total.
    attempt = {
        "kind": "quiz",
        "quizId": quiz["id"],
        "attempt": attempt_number,
        "passed": passed,
        "grade": grade_decimal,                # 0-1 decimal, e.g. 0.9
        "achievedScore": correct_count,        # questions correct
        "totalScore": question_count,          # questions total
        "ksbs": ksbs,                          # KSB codes the learner selected
        "feedback": feedback,
        "reportedTime": reported_time,
        "reflectionSkipped": reflection_skipped,
        "questions": stored_questions,         # id-referenced (see above)
        "startedAt": started_at,
        "submittedAt": submitted_at,
        "timeTaken": time_taken,
        "timeTrackingSource": tracking["source"],
        "timeTrackingCalculation": tracking["calculation"],
        "timeTrackingSessionId": tracking["sessionId"],
        "claimedSeconds": tracking["claimedSeconds"],
        "serverSessionSeconds": tracking["serverSessionSeconds"],
        "verifiedSeconds": tracking["verifiedSeconds"],
        # submittedAt stays the real Submit click; declaredCompletedAt is the
        # working instant OTJH and reporting count the attempt at when that
        # click had to be corrected, and the reason says why it was corrected.
        "declaredCompletedAt": declared_at.isoformat() if declared_at else None,
        "submissionValidationReason": validation_reason,
        "retakeTimeChoice": retake_choice,
    }

    if active is not None:
        activity = {
            "kind": "quiz",
            "action": "Completed quiz",
            "title": quiz["title"],
            "detail": f"Scored {int(grade_pct) if grade_pct == int(grade_pct) else grade_pct}% · {correct_count}/{question_count}",
            "passed": passed,
            "quizId": quiz["id"],
            "week": week_title,
            "module": module_title or quiz["module"],
            "at": submitted_at,
        }
        try:
            save_progress_record(active, attempt, activity)
        except ComponentReferenceError as exc:
            return _error(str(exc), 400)
        except IntegrityError as exc:
            if "learner_progress_tracking_session_uq" in str(exc):
                return _error("This activity timing session has already been submitted.", 409)
            return _error(f"Database error saving attempt: {exc}", 502)
        except DatabaseError as exc:
            return _error(f"Database error saving attempt: {exc}", 502)
        record_quiz_attempt(
            kind=kind, learner_id=learner_id, source=source, quiz=quiz,
            attempt_number=attempt_number, passed=passed, grade_pct=grade_pct,
            correct_count=correct_count, question_count=question_count, time_taken=time_taken,
            submitted_at=submitted_at_dt, module_title=module_title, week_title=week_title,
        )
        # Engagement points award themselves: save_progress_record registers a
        # post-commit hook (engagement_api.hooks.award_for_progress) that fires
        # once this save actually commits — see active_users.save_progress_record.

    # The response carries the FULL-text breakdown + summary so the results
    # screen can render immediately without a second lookup.
    return JsonResponse({
        "attempt": attempt,
        "breakdown": breakdown,
        "earned": earned,
        "possible": possible,
        "grade": grade_decimal,
        "achievedScore": correct_count,
        "totalScore": question_count,
        "passed": passed,
        "timeTaken": time_taken,
        "quizName": quiz["title"],
    })
