import asyncio
from datetime import UTC, datetime

import pytest
from beanie import PydanticObjectId

from app.models import Submission, UserRole
from app.services.grading import PlaceholderGradingService, TransientGradingError
from app.services.grading_worker import GradingWorker
from tests.integration import test_submission_management as fixtures
from tests.integration.test_grading_worker import enqueue

submission_work = fixtures.submission_work
pytestmark = pytest.mark.integration
PAST = datetime(2000, 1, 1, tzinfo=UTC)


class UnavailableGrader:
    async def grade_short_answer(self, *args):
        raise TransientGradingError("secret must not be persisted")


async def make_due(submission):
    await Submission.get_pymongo_collection().update_one(
        {"_id": submission.id}, {"$set": {"ai_next_attempt_at": PAST, "ai_lease_until": PAST}},
    )


async def test_transient_retry_backoff_and_success(api_client, submission_work, test_settings):
    submission = await enqueue(api_client, submission_work)
    worker = GradingWorker(test_settings, UnavailableGrader())
    for attempt in [1, 2]:
        assert await worker.run_once()
        stored = await Submission.get(submission.id)
        assert stored.ai_status == "pending" and stored.ai_attempts == attempt
        assert stored.ai_error_code == "provider_unavailable" and stored.ai_next_attempt_at
        assert not await worker.run_once()
        await make_due(submission)
    worker.grader = PlaceholderGradingService()
    assert await worker.run_once()
    stored = await Submission.get(submission.id)
    assert stored.ai_attempts == 3 and stored.ai_status == "succeeded"
    assert stored.ai_next_attempt_at is None and stored.ai_error_code is None


@pytest.mark.parametrize("crash", [False, True])
async def test_recovery_and_failure_share_finite_budget(api_client, submission_work, test_settings, crash):
    submission = await enqueue(api_client, submission_work)
    worker = GradingWorker(test_settings, UnavailableGrader())
    for _ in range(3):
        if crash:
            assert await worker.claim() is not None
        else:
            assert await worker.run_once()
        await make_due(submission)
    assert not await worker.run_once()
    stored = await Submission.get(submission.id)
    assert stored.ai_attempts == 3 and stored.ai_status == "failed"
    assert stored.ai_error_code == ("retry_exhausted" if crash else "provider_unavailable")
    assert stored.answers[0].answer_text == "Example" and stored.ai_total_score is None


async def test_manual_retry_permissions_and_replay_guard(api_client, submission_work, auth_headers, test_settings):
    work = submission_work
    submission = await enqueue(api_client, work)
    await submission.set({"ai_status": "failed", "ai_attempts": 3, "ai_cycle_attempts": 3})
    path = f"/api/v1/submissions/{submission.id}/retry-grading"
    payload = {"expected_retry_count": 0}
    assert (await api_client.post(path, json=payload)).status_code == 401
    assert (await api_client.post(path, headers=work["student"], json=payload)).status_code == 403
    original_teacher = work["group"].teacher_id
    await work["group"].set({"teacher_id": PydanticObjectId()})
    assert (await api_client.post(path, headers=work["teacher"], json=payload)).status_code == 403
    await work["group"].set({"teacher_id": original_teacher})
    admin = await auth_headers(UserRole.admin)
    results = await asyncio.gather(*[
        api_client.post(path, headers=headers, json=payload) for headers in [work["teacher"], admin]
    ])
    assert sorted(r.status_code for r in results) == [202, 409]
    stored = await Submission.get(submission.id)
    assert stored.ai_attempts == 3 and stored.ai_cycle_attempts == 0 and stored.ai_retry_count == 1
    assert not await GradingWorker(test_settings, UnavailableGrader()).run_once()
    await stored.set({"ai_status": "failed"})
    assert (await api_client.post(path, headers=admin, json=payload)).status_code == 409
    assert (await api_client.post(path, headers=admin, json={"expected_retry_count": 1})).status_code == 202
    await make_due(submission)
    await GradingWorker(test_settings, PlaceholderGradingService()).run_once()
    stored = await Submission.get(submission.id)
    assert stored.ai_attempts == 4 and stored.ai_cycle_attempts == 1
    student = (await api_client.get(work["path"] + "/submissions/my", headers=work["student"])).json()
    assert not any(k.startswith("ai_") for k in student)


async def test_retry_rejects_other_states_and_confirmed(api_client, submission_work):
    work = submission_work
    submission = await enqueue(api_client, work)
    path = f"/api/v1/submissions/{submission.id}"
    for state in ["pending", "processing", "succeeded", "cancelled", "legacy_unknown"]:
        await submission.set({"ai_status": state})
        assert (await api_client.post(path + "/retry-grading", headers=work["teacher"],
                                      json={"expected_retry_count": 0})).status_code == 409
    await submission.set({"ai_status": "failed"})
    confirmed = await api_client.post(path + "/confirm-grade", headers=work["teacher"],
                                     json={"grades": [{"question_id": str(work["question"].id), "final_score": 7}]})
    assert confirmed.status_code == 200
    assert (await api_client.post(path + "/retry-grading", headers=work["teacher"],
                                  json={"expected_retry_count": 0})).status_code == 409


async def test_legacy_attempt_budget_is_not_reset(api_client, submission_work, test_settings):
    submission = await enqueue(api_client, submission_work)
    await Submission.get_pymongo_collection().update_one({"_id": submission.id}, {
        "$set": {"ai_attempts": 3}, "$unset": {"ai_cycle_attempts": ""},
    })
    assert not await GradingWorker(test_settings, UnavailableGrader()).run_once()
    assert (await Submission.get(submission.id)).ai_status == "failed"


async def test_retry_racing_confirmation_cannot_reopen_grade(api_client, submission_work):
    work = submission_work
    submission = await enqueue(api_client, work)
    await submission.set({"ai_status": "failed"})
    path = f"/api/v1/submissions/{submission.id}"
    retry, confirm = await asyncio.gather(
        api_client.post(path + "/retry-grading", headers=work["teacher"], json={"expected_retry_count": 0}),
        api_client.post(path + "/confirm-grade", headers=work["teacher"],
                        json={"grades": [{"question_id": str(work["question"].id), "final_score": 7}]}),
    )
    assert retry.status_code in [202, 409] and confirm.status_code == 200
    stored = await Submission.get(submission.id)
    assert stored.status == "confirmed" and stored.ai_status == "cancelled"
    assert stored.ai_next_attempt_at is None and stored.final_total_score == 7
