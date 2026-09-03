"""Background worker that processes exercise evaluation jobs from Redis."""
from __future__ import annotations

import asyncio
import logging
import re
import sys

from app.core.config import settings
from app.core.database import async_session
from app.models.lesson import Exercise, Lesson
from app.models.study_plan import StudyPlan
from app.services.eval_queue import dequeue_exercise
from app.services.lesson_generator import (
    evaluate_fill_blank,
    evaluate_free_write,
    evaluate_pronunciation,
)
from app.services.llm_adapter import LLMError, LLMTimeoutError, LLMUnavailableError
from app.services.progress_service import update_daily_progress, upsert_unit_competency

logger = logging.getLogger("eval_worker")

_ANSWER_FEEDBACK: dict[str, dict[str, str]] = {
    "en": {
        "correct": "Correct!",
        "correct_answer": "The correct answer is: {answer}",
        "free_write_unavailable": "Could not evaluate free-write answer at this time.",
        "good_pronunciation": "Good pronunciation!",
        "target_phrase": "The target phrase was: {answer}",
    },
    "vi": {
        "correct": "Dung!",
        "correct_answer": "Dap an dung la: {answer}",
        "free_write_unavailable": "Khong the danh gia cau tra loi vao luc nay.",
        "good_pronunciation": "Phat am tot!",
        "target_phrase": "Cau muc tieu la: {answer}",
    },
}


def _answer_feedback(native_language: str, key: str, answer: str = "") -> str:
    messages = _ANSWER_FEEDBACK.get(native_language, _ANSWER_FEEDBACK["en"])
    return messages.get(key, key).format(answer=answer)


async def _evaluate(exercise: Exercise, lesson: Lesson, native_language: str) -> None:
    """Run the appropriate evaluation for an exercise type."""
    target_language = "en-GB"
    if lesson.study_plan_id:
        async with async_session() as db:
            plan = await db.get(StudyPlan, lesson.study_plan_id)
            if plan:
                target_language = plan.target_language

    if exercise.exercise_type == "free_write":
        criteria = [opt for opt in (exercise.options or []) if isinstance(opt, str) and opt.strip()]
        if not criteria:
            criteria = ["grammar", "spelling", "coherence"]
        try:
            eval_result = await evaluate_free_write(
                cefr_level=lesson.cefr_level,
                prompt=exercise.question,
                criteria=criteria,
                answer=exercise.user_answer,
                target_language=target_language,
                native_language=native_language,
            )
            exercise.score = eval_result.score if hasattr(eval_result, "score") else eval_result["score"]
            exercise.feedback = (
                eval_result.feedback
                if hasattr(eval_result, "feedback")
                else eval_result["feedback"]
            )
        except (LLMTimeoutError, LLMUnavailableError, LLMError):
            exercise.score = 0.5
            exercise.feedback = _answer_feedback(native_language, "free_write_unavailable")

    elif exercise.exercise_type == "fill_blank":
        try:
            eval_result = await evaluate_fill_blank(
                cefr_level=lesson.cefr_level,
                question=exercise.question,
                correct_answer=exercise.correct_answer,
                student_answer=exercise.user_answer,
                target_language=target_language,
                native_language=native_language,
            )
            exercise.score = eval_result.score
            exercise.feedback = eval_result.feedback
        except (LLMTimeoutError, LLMUnavailableError, LLMError):
            ua = (exercise.user_answer or "").strip().lower().rstrip(".,!?")
            ca = exercise.correct_answer.strip().lower().rstrip(".,!?")
            alternatives = [a.strip().lower() for a in ca.split("/")]
            is_correct = ua == ca or ua in alternatives
            exercise.score = 1.0 if is_correct else 0.0
            exercise.feedback = (
                _answer_feedback(native_language, "correct")
                if is_correct
                else _answer_feedback(native_language, "correct_answer", answer=exercise.correct_answer)
            )

    elif exercise.exercise_type == "pronunciation":
        try:
            eval_result = await evaluate_pronunciation(
                cefr_level=lesson.cefr_level,
                target=exercise.correct_answer,
                transcription=exercise.user_answer or "",
                target_language=target_language,
                native_language=native_language,
            )
            exercise.score = eval_result.score
            exercise.feedback = eval_result.feedback
        except (LLMTimeoutError, LLMUnavailableError, LLMError):
            norm_target = re.sub(r"[^\w\s]", "", exercise.correct_answer).strip().lower()
            norm_answer = re.sub(r"[^\w\s]", "", (exercise.user_answer or "")).strip().lower()
            is_close = (
                norm_target == norm_answer
                or norm_target in norm_answer
                or norm_answer in norm_target
            )
            exercise.score = 1.0 if is_close else 0.0
            exercise.feedback = (
                _answer_feedback(native_language, "good_pronunciation")
                if is_close
                else _answer_feedback(native_language, "target_phrase", answer=exercise.correct_answer)
            )
    else:
        exercise.score = 0.5
        exercise.feedback = "Evaluation not available for this exercise type."


async def _process_job(job: dict) -> None:
    exercise_id = job["exercise_id"]
    async with async_session() as db:
        exercise = await db.get(Exercise, exercise_id)
        if exercise is None:
            logger.warning("Exercise %d not found, skipping", exercise_id)
            return
        if exercise.eval_status == "completed":
            logger.info("Exercise %d already completed, skipping", exercise_id)
            return

        lesson = await db.get(Lesson, exercise.lesson_id)
        if lesson is None:
            logger.warning("Lesson %d not found for exercise %d", exercise.lesson_id, exercise_id)
            return

        # Mark as processing
        exercise.eval_status = "processing"
        await db.commit()

        # Determine native language
        from app.models.user import User
        user = await db.get(User, job.get("user_id", 0))
        native_language = user.native_language if user else "en"

        try:
            await _evaluate(exercise, lesson, native_language)
            exercise.eval_status = "completed"
        except Exception:
            logger.exception("Evaluation failed for exercise %d", exercise_id)
            exercise.score = 0.5
            exercise.feedback = "Evaluation temporarily unavailable."
            exercise.eval_status = "failed"

        await db.commit()
        logger.info("Exercise %d evaluated: score=%s status=%s", exercise_id, exercise.score, exercise.eval_status)

        # Update progress after evaluation completes
        if exercise.eval_status == "completed" and exercise.score is not None:
            await update_daily_progress(
                db,
                job.get("user_id", 0),
                exercise_correct=exercise.score >= 0.5,
                skill=lesson.lesson_type,
                skill_score=exercise.score,
                study_plan_id=lesson.study_plan_id,
            )


async def run_worker() -> None:
    logger.info("Eval worker started, listening on %s", settings.REDIS_URL)
    while True:
        try:
            job = await dequeue_exercise()
            if job is not None:
                await _process_job(job)
        except Exception:
            logger.exception("Unexpected error in eval worker")
            await asyncio.sleep(1)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
