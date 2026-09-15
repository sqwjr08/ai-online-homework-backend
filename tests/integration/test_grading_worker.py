import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models import Submission
from app.services.grading import GradeResult, PlaceholderGradingService
from app.services.grading_worker import GradingWorker
from tests.integration import test_submission_management as submission_fixtures

submission_work = submission_fixtures.submission_work

pytestmark = pytest.mark.integration


async def enqueue(api_client, work):
    response = await api_client.post(work["path"] + "/submissions", headers=work["student"], json=work["payload"])
    assert response.status_code == 201
    return await Submission.find_one({"assignment_id": work["id"]})


async def test_worker_claim_is_exclusive_and_saves_draft(api_client, submission_work, test_settings):
    submission = await enqueue(api_client, submission_work)
    workers = [GradingWorker(test_settings, PlaceholderGradingService()) for _ in range(2)]
    claims = await asyncio.gather(*(w.claim() for w in workers))
    assert sum(c is not None for c in claims) == 1
    await workers[0].execute(next(c for c in claims if c))
    stored = await Submission.get(submission.id)
    assert stored.ai_status == "succeeded" and stored.ai_attempts == 1
    assert stored.ai_total_score == 10 and stored.answers[0].answer_text == "Example"
    assert stored.final_total_score is None and stored.status == "pending_teacher_review"
    assert not await workers[0].run_once()
    index = (await Submission.get_pymongo_collection().index_information())["ai_work_queue"]
    assert index["key"][0] == ("status", 1)


@pytest.mark.parametrize("failure", ["timeout", "exception", "invalid"])
async def test_worker_failure_preserves_answer(api_client, submission_work, test_settings, failure):
    submission = await enqueue(api_client, submission_work)

    class BrokenGrader:
        async def grade_short_answer(self, *args):
            if failure == "timeout":
                await asyncio.sleep(10)
            if failure == "exception":
                raise RuntimeError("secret provider credential")
            return GradeResult(float("nan"), "bad")

    settings = test_settings.model_copy(update={"ai_job_timeout_seconds": 0.02})
    worker = GradingWorker(settings, BrokenGrader())
    assert await worker.run_once()
    stored = await Submission.get(submission.id)
    assert stored.ai_status == ("pending" if failure == "timeout" else "failed")
    assert stored.ai_total_score is None
    assert stored.ai_error_code == ("timeout" if failure == "timeout" else "grading_failed")
    assert stored.answers[0].answer_text == "Example" and stored.answers[0].ai_score is None
    assert not await worker.run_once()  # Backoff or terminal failures cannot be claimed.


async def test_restart_reclaims_expired_lease_and_fences_old_result(api_client, submission_work, test_settings):
    submission = await enqueue(api_client, submission_work)
    worker = GradingWorker(test_settings, PlaceholderGradingService())
    old = await worker.claim()
    assert await worker.claim() is None
    await Submission.get_pymongo_collection().update_one(
        {"_id": submission.id}, {"$set": {"ai_lease_until": datetime(2000, 1, 1, tzinfo=UTC)}},
    )
    assert not await worker.finish(old, {"ai_status": "succeeded", "ai_total_score": 1})
    restarted = GradingWorker(test_settings, PlaceholderGradingService())
    new = await restarted.claim()
    assert new["ai_token"] != old["ai_token"] and new["ai_attempts"] == 2
    assert not await worker.finish(old, {"ai_status": "failed"})
    await restarted.execute(new)
    assert (await Submission.get(submission.id)).ai_total_score == 10


async def test_confirmation_during_grading_discards_late_ai(api_client, submission_work, test_settings):
    work = submission_work
    submission = await enqueue(api_client, work)
    started, release = asyncio.Event(), asyncio.Event()

    class WaitingGrader:
        async def grade_short_answer(self, *args):
            started.set()
            await release.wait()
            return GradeResult(10, "Late draft")

    worker = GradingWorker(test_settings, WaitingGrader())
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(started.wait(), 2)
        result = await api_client.post(f"/api/v1/submissions/{submission.id}/confirm-grade", headers=work["teacher"],
                                      json={"grades": [{"question_id": str(work["question"].id),
                                                       "final_score": 6, "final_comment": "$literal feedback"}]})
        assert result.status_code == 200, result.text
    finally:
        release.set()
        await task
    stored = await Submission.get(submission.id)
    assert stored.ai_status == "cancelled" and stored.ai_total_score is None
    assert stored.final_total_score == 6 and stored.answers[0].final_comment == "$literal feedback"


async def test_cancelled_execution_recovers_and_legacy_is_not_claimed(api_client, submission_work, test_settings):
    submission = await enqueue(api_client, submission_work)

    class CancelledGrader:
        async def grade_short_answer(self, *args):
            raise asyncio.CancelledError

    worker = GradingWorker(test_settings, CancelledGrader())
    with pytest.raises(asyncio.CancelledError):
        await worker.run_once()
    stored = await Submission.get(submission.id)
    assert stored.ai_status == "processing" and stored.ai_lease_until is not None
    await Submission.get_pymongo_collection().update_one(
        {"_id": submission.id}, {"$unset": {"ai_status": ""}},
    )
    assert await worker.claim() is None


async def test_worker_module_runs_in_separate_process(api_client, submission_work):
    submission = await enqueue(api_client, submission_work)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.worker", cwd=Path(__file__).resolve().parents[2],
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            while (await Submission.get(submission.id)).ai_status != "succeeded":
                assert process.returncode is None, "Worker exited before grading"
                await asyncio.sleep(0.1)
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


async def test_ai_completed_during_confirmation_is_preserved(api_client, submission_work, test_settings, monkeypatch):
    import app.services.submissions as service

    work = submission_work
    submission = await enqueue(api_client, work)
    original = service.assignment_content

    async def complete_ai(assignment):
        await GradingWorker(test_settings, PlaceholderGradingService()).run_once()
        return await original(assignment)

    monkeypatch.setattr(service, "assignment_content", complete_ai)
    result = await api_client.post(f"/api/v1/submissions/{submission.id}/confirm-grade", headers=work["teacher"],
                                  json={"grades": [{"question_id": str(work["question"].id), "final_score": 7}]})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["ai_status"] == "succeeded" and data["ai_total_score"] == 10
    assert data["answers"][0]["ai_score"] == 10 and data["final_total_score"] == 7
