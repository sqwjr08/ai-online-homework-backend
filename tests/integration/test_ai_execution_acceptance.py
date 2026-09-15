import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from app.models import User, UserRole
from app.services.grading import GradeResult, TransientGradingError
from app.services.grading_worker import GradingWorker
from tests.integration import test_backend_acceptance as http_helpers

live_client = http_helpers.live_client
request = http_helpers.request
login = http_helpers.login
pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ai_work(live_client, account_password, account_password_hash, test_settings):
    client = live_client
    test_settings.ai_retry_base_seconds = 1
    test_settings.ai_poll_seconds = 0.02
    test_settings.ai_job_timeout_seconds = 1
    test_settings.ai_lease_seconds = 2
    # Bootstrap only; every business operation below uses real TCP/HTTP.
    await User(username="ai_admin", password_hash=account_password_hash, role=UserRole.admin).insert()
    admin = await login(client, "ai_admin", account_password)
    for name in ["ai_teacher", "ai_other_teacher"]:
        await request(client, "POST", "/api/v1/users", 201, admin,
                      json={"username": name, "password": account_password, "role": "teacher"})
    teacher = await login(client, "ai_teacher", account_password)
    other = await login(client, "ai_other_teacher", account_password)
    await request(client, "POST", "/api/v1/auth/register", 201,
                  json={"username": "ai_student", "password": account_password})
    student = await login(client, "ai_student", account_password)
    group = await request(client, "POST", "/api/v1/classes", 201, teacher, json={"name": "AI example class"})
    await request(client, "POST", "/api/v1/classes/join", headers=student, json={"code": group["code"]})
    questions = []
    for prompt, maximum in [("Explain an index", 10), ("Explain a transaction", 5)]:
        questions.append(await request(client, "POST", "/api/v1/questions", 201, teacher,
                                       json={"prompt": prompt, "reference_answer": "SECRET_REFERENCE",
                                             "rubric": "SECRET_RUBRIC", "max_score": maximum}))
    work = await request(client, "POST", "/api/v1/assignments", 201, teacher, json={
        "title": "AI execution example", "class_id": group["id"], "status": "draft",
        "questions": [{"question_id": q["id"]} for q in questions],
    })
    path = f"/api/v1/assignments/{work['id']}"
    await request(client, "POST", path + "/publish", headers=teacher)
    submitted = await request(client, "POST", path + "/submissions", 201, student, json={
        "answers": [{"question_id": q["id"], "answer_text": "Example explanation"} for q in questions],
    })
    return {"client": client, "teacher": teacher, "admin": admin, "student": student, "other": other,
            "path": f"/api/v1/submissions/{submitted['id']}", "work_path": path, "questions": questions}


async def wait_state(work, predicate, timeout=12):
    async with asyncio.timeout(timeout):
        while True:
            data = await request(work["client"], "GET", work["path"], headers=work["teacher"])
            if predicate(data):
                return data
            await asyncio.sleep(0.03)


async def check_student(work, final=None):
    for path in [work["path"], work["work_path"] + "/submissions/my"]:
        data = await request(work["client"], "GET", path, headers=work["student"])
        assert data["final_total_score"] == final
        assert not any(k.startswith("ai_") for k in data)
        assert all(not any(k.startswith("ai_") for k in answer) for answer in data["answers"])
        assert all(answer["answer_text"] == "Example explanation" for answer in data["answers"])
        assert "SECRET_" not in str(data)


async def test_live_failed_job_retry_confirmation_and_score(ai_work, test_settings):
    work = ai_work
    client, path = work["client"], work["path"]
    initial = await wait_state(work, lambda d: d["ai_status"] == "pending")
    assert initial["ai_attempts"] == 0
    await check_student(work)

    class FailSecondQuestion:
        failures = 0

        async def grade_short_answer(self, question, answer_text):
            if question.max_score == 5 and self.failures < 3:
                self.failures += 1
                raise TransientGradingError("SECRET_PROVIDER_FAILURE")
            return GradeResult(question.max_score, "Draft example")

    task = asyncio.create_task(GradingWorker(test_settings, FailSecondQuestion()).run())
    try:
        waiting = await wait_state(work, lambda d: d["ai_status"] == "pending" and d["ai_attempts"] == 1)
        assert waiting["ai_next_attempt_at"] and waiting["ai_error_code"] == "provider_unavailable"
        assert waiting["ai_total_score"] is None
        assert all(a["ai_score"] is None and a["ai_comment"] is None for a in waiting["answers"])
        failed = await wait_state(work, lambda d: d["ai_status"] == "failed")
        assert failed["ai_attempts"] == 3 and "SECRET_PROVIDER_FAILURE" not in str(failed)
        assert all(a["ai_score"] is None for a in failed["answers"])
        await check_student(work)
        payload = {"expected_retry_count": 0}
        await request(client, "POST", path + "/retry-grading", 401, json=payload)
        for actor in [work["student"], work["other"]]:
            await request(client, "POST", path + "/retry-grading", 403, actor, json=payload)
        results = await asyncio.gather(*[
            client.post(path + "/retry-grading", headers=actor, json=payload)
            for actor in [work["teacher"], work["admin"]]
        ])
        assert sorted(r.status_code for r in results) == [202, 409]
        complete = await wait_state(work, lambda d: d["ai_status"] == "succeeded")
        assert complete["ai_attempts"] == 4 and complete["ai_retry_count"] == 1
        assert complete["ai_total_score"] == 15 and complete["final_total_score"] is None
        await check_student(work)
        grades = {"grades": [{"question_id": q["id"], "final_score": score, "final_comment": "Reviewed"}
                              for q, score in zip(work["questions"], [9, 4], strict=True)]}
        confirmed = await request(client, "POST", path + "/confirm-grade", headers=work["teacher"], json=grades)
        assert confirmed["final_total_score"] == 13 and confirmed["ai_total_score"] == 15
        for actor in [work["teacher"], work["admin"]]:
            await request(client, "POST", path + "/confirm-grade", 409, actor, json=grades)
            await request(client, "POST", path + "/retry-grading", 409, actor, json={"expected_retry_count": 1})
        await check_student(work, 13)
        schema = (await client.get("/openapi.json")).json()
        operation = schema["paths"]["/api/v1/submissions/{submission_id}/retry-grading"]["post"]
        assert {"202", "401", "403", "404", "409", "422"} <= operation["responses"].keys()
        assert "expected_retry_count" in schema["components"]["schemas"]["RetryGradingRequest"]["required"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_live_worker_process_restart_recovers_saved_submission(ai_work):
    work = ai_work
    env = {**os.environ, "AI_JOB_TIMEOUT_SECONDS": "1", "AI_LEASE_SECONDS": "2",
           "AI_RETRY_BASE_SECONDS": "1", "AI_POLL_SECONDS": "0.05", "AI_MAX_ATTEMPTS": "3"}
    # Test-only provider stalls after the real CLI claims a job, before result persistence.
    script = """
import asyncio
import app.worker as entry
class PausedProvider:
    async def grade_short_answer(self, *args):
        await asyncio.sleep(60)
entry.get_grading_service = lambda: PausedProvider()
asyncio.run(entry.main())
"""
    processes = []

    async def start(*args):
        process = await asyncio.create_subprocess_exec(
            sys.executable, *args, env=env, cwd=Path(__file__).resolve().parents[2],
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    try:
        first = await start("-c", script)
        processing = await wait_state(work, lambda d: d["ai_status"] == "processing")
        assert processing["ai_attempts"] == 1
        first.terminate()
        await first.wait()
        await check_student(work)
        await start("-m", "app.worker")
        recovered = await wait_state(work, lambda d: d["ai_status"] == "succeeded")
        assert recovered["ai_attempts"] == 2 and recovered["ai_retry_count"] == 0
        assert recovered["final_total_score"] is None
        await check_student(work)
        grades = {"grades": [{"question_id": q["id"], "final_score": 4} for q in work["questions"]]}
        await request(work["client"], "POST", work["path"] + "/confirm-grade", headers=work["teacher"], json=grades)
        await check_student(work, 8)
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
            await process.wait()
