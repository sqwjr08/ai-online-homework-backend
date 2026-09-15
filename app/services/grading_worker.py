import asyncio
import logging
import math
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.models import Assignment, Submission
from app.services.assignments import assignment_content
from app.services.grading import GradingService, TransientGradingError

logger = logging.getLogger(__name__)


def cycle_attempts() -> dict:
    # 14b records have only the lifetime counter; preserve their already-used budget.
    return {"$ifNull": ["$ai_cycle_attempts", {"$ifNull": ["$ai_attempts", 0]}]}


class GradingWorker:
    def __init__(self, settings: Settings, grader: GradingService):
        self.settings = settings
        self.grader = grader

    async def claim(self) -> dict | None:
        expired = {"ai_status": "processing", "ai_lease_until": {"$type": "date"},
                   "$expr": {"$lte": ["$ai_lease_until", "$$NOW"]}}
        collection = Submission.get_pymongo_collection()
        await collection.update_many(
            {"status": "pending_teacher_review", "$and": [
                {"$or": [{"ai_status": "pending"}, expired]},
                {"$expr": {"$gte": [cycle_attempts(), self.settings.ai_max_attempts]}},
            ]},
            {"$set": {"ai_status": "failed", "ai_error_code": "retry_exhausted",
                      "ai_token": None, "ai_lease_until": None, "ai_next_attempt_at": None}},
        )
        delay = {"$multiply": [self.settings.ai_retry_base_seconds * 1000,
                               {"$pow": [2, {"$min": [9, {"$max": [0, {"$subtract": [cycle_attempts(), 1]}]}]}]}]}
        # MongoDB's clock controls leases across workers; no in-memory queue is required.
        return await collection.find_one_and_update(
            {"status": "pending_teacher_review",
             "$expr": {"$lt": [cycle_attempts(), self.settings.ai_max_attempts]}, "$or": [
                {"ai_status": "pending", "$expr": {"$lte": [
                    {"$ifNull": ["$ai_next_attempt_at", "$$NOW"]}, "$$NOW",
                ]}},
                {"ai_status": "processing", "ai_lease_until": {"$type": "date"},
                 "$expr": {"$lte": [{"$add": ["$ai_lease_until", delay]}, "$$NOW"]}},
            ]},
            [{"$set": {
                "ai_status": "processing", "ai_token": uuid4().hex,
                "ai_lease_until": {"$add": ["$$NOW", self.settings.ai_lease_seconds * 1000]},
                "ai_attempts": {"$add": [{"$ifNull": ["$ai_attempts", 0]}, 1]},
                "ai_cycle_attempts": {"$add": [cycle_attempts(), 1]},
                "ai_error_code": None, "ai_next_attempt_at": None,
            }}],
            sort=[("submitted_at", 1), ("_id", 1)], return_document=ReturnDocument.AFTER,
        )

    async def finish(self, job: dict, changes: dict, retry_delay: int | None = None) -> bool:
        changes = {**changes, "ai_token": None, "ai_lease_until": None, "ai_next_attempt_at": None}
        update = {"$set": changes}
        if retry_delay is not None:
            update = [{"$set": {**{key: {"$literal": value} for key, value in changes.items()},
                                "ai_next_attempt_at": {"$add": ["$$NOW", retry_delay * 1000]}}}]
        result = await Submission.get_pymongo_collection().update_one(
            {"_id": job["_id"], "status": "pending_teacher_review", "ai_status": "processing",
             "ai_token": job["ai_token"], "$expr": {"$gt": ["$ai_lease_until", "$$NOW"]}},
            update,
        )
        return result.modified_count == 1

    async def grade(self, job: dict) -> dict:
        submission = Submission.model_validate(job)
        assignment = await Assignment.get(submission.assignment_id)
        if assignment is None:
            raise ValueError("Missing assignment")
        source, contents = await assignment_content(assignment)
        if source == "draft_preview":
            raise ValueError("Unpublished assignment")
        by_id = dict(zip([q.question_id for q in assignment.questions], contents, strict=True))
        ids = [a.question_id for a in submission.answers]
        if not ids or len(set(ids)) != len(ids) or set(ids) != set(by_id):
            raise ValueError("Invalid answer set")
        changes = {}
        total = 0.0
        for index, answer in enumerate(submission.answers):
            question = by_id[answer.question_id]
            grade = await self.grader.grade_short_answer(question, answer.answer_text)
            if (isinstance(grade.score, bool) or not isinstance(grade.score, (int, float))
                    or not math.isfinite(grade.score) or not 0 <= grade.score <= question.max_score
                    or not isinstance(grade.comment, str) or not grade.comment.strip()
                    or len(grade.comment) > 2000):
                raise ValueError("Invalid provider result")
            score = round(grade.score, 2)
            total += score
            changes[f"answers.{index}.ai_score"] = score
            changes[f"answers.{index}.ai_comment"] = grade.comment
        if not math.isfinite(total):
            raise ValueError("Invalid total")
        return {**changes, "ai_status": "succeeded", "ai_total_score": round(total, 2),
                "ai_error_code": None}

    async def execute(self, job: dict) -> None:
        retryable = False
        try:
            async with asyncio.timeout(self.settings.ai_job_timeout_seconds):
                changes = await self.grade(job)
        except TimeoutError:
            retryable = True
            changes = {"ai_status": "failed", "ai_error_code": "timeout"}
        except TransientGradingError:
            retryable = True
            changes = {"ai_status": "failed", "ai_error_code": "provider_unavailable"}
        except Exception:
            # Never persist/log provider exception text, which may contain answers or credentials.
            changes = {"ai_status": "failed", "ai_error_code": "grading_failed"}
        attempts = job.get("ai_cycle_attempts") or job.get("ai_attempts", 1)
        retry_delay = None
        if retryable and attempts < self.settings.ai_max_attempts:
            retry_delay = self.settings.ai_retry_base_seconds * 2 ** (attempts - 1)
            changes["ai_status"] = "pending"
        await self.finish(job, changes, retry_delay)

    async def run_once(self) -> bool:
        job = await self.claim()
        if job is None:
            return False
        await self.execute(job)
        return True

    async def run(self) -> None:
        while True:
            try:
                worked = await self.run_once()
            except PyMongoError:
                logger.warning("Grading database unavailable; polling will resume")
                worked = False
            if not worked:
                await asyncio.sleep(self.settings.ai_poll_seconds)
            else:
                await asyncio.sleep(0)
