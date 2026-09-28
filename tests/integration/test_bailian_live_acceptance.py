"""Explicitly opt-in, one-request acceptance against the real Bailian API."""

import asyncio
import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import User, UserRole
from app.services.grading import get_grading_service
from app.services.grading_worker import GradingWorker
from tests.integration import test_backend_acceptance as helpers

live_client = helpers.live_client
request = helpers.request
login = helpers.login
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ALLOW_BAILIAN_LIVE_ACCEPTANCE") != "one-request",
        reason="Requires explicit authorization for one billable API request",
    ),
]


async def test_real_bailian_homework_flow(
    live_client, test_settings, account_password, account_password_hash,
):
    local = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env")
    assert local.bailian_api_key is not None, "Configure a local Bailian key first"
    assert local.bailian_model == "qwen3.8-flash"
    test_settings.ai_provider = "bailian"
    for name in Settings.model_fields:
        if name.startswith("bailian_"):
            setattr(test_settings, name, getattr(local, name))
    test_settings.ai_max_attempts = 1
    test_settings.ai_job_timeout_seconds = 60
    test_settings.ai_lease_seconds = 90
    test_settings.ai_poll_seconds = 0.05
    client = live_client
    # Only the administrator bootstrap bypasses HTTP; all business writes use the API.
    await User(username="live_admin", password_hash=account_password_hash, role=UserRole.admin).insert()
    admin = await login(client, "live_admin", account_password)
    await request(client, "POST", "/api/v1/users", 201, admin, json={
        "username": "live_teacher", "password": account_password, "role": "teacher",
    })
    teacher = await login(client, "live_teacher", account_password)
    await request(client, "POST", "/api/v1/auth/register", 201, json={
        "username": "live_student", "password": account_password,
    })
    student = await login(client, "live_student", account_password)
    group = await request(client, "POST", "/api/v1/classes", 201, teacher,
                          json={"name": "Fictional AI acceptance class"})
    await request(client, "POST", "/api/v1/classes/join", headers=student,
                  json={"code": group["code"]})
    question = await request(client, "POST", "/api/v1/questions", 201, teacher, json={
        "prompt": "Explain the purpose and costs of a database index.",
        "reference_answer": "Indexes speed up record lookup, consume storage and add write maintenance costs.",
        "rubric": "Faster lookup: 4 points. Extra storage: 3 points. Write maintenance cost: 3 points.",
        "max_score": 10,
    })
    assignment = await request(client, "POST", "/api/v1/assignments", 201, teacher, json={
        "title": "Fictional one-question homework", "class_id": group["id"], "status": "draft",
        "questions": [{"question_id": question["id"]}],
    })
    assignment_path = f"/api/v1/assignments/{assignment['id']}"
    await request(client, "POST", assignment_path + "/publish", headers=teacher)
    submitted = await request(client, "POST", assignment_path + "/submissions", 201, student, json={
        "answers": [{"question_id": question["id"], "answer_text":
                     "An index speeds up finding records but takes additional storage. "
                     "Inserts, updates and deletes must maintain it, adding write overhead."}],
    })
    path = f"/api/v1/submissions/{submitted['id']}"

    async def student_view(final=None):
        for endpoint in [path, assignment_path + "/submissions/my"]:
            view = await request(client, "GET", endpoint, headers=student)
            assert view["final_total_score"] == final
            assert not any(key.startswith("ai_") for key in view)
            assert all(not any(key.startswith("ai_") for key in answer) for answer in view["answers"])
            if final is None:
                assert all(answer["final_score"] is None for answer in view["answers"])
            else:
                assert view["answers"][0]["final_comment"] == "Teacher reviewed: all criteria met."

    initial = await request(client, "GET", path, headers=teacher)
    assert initial["ai_status"] == "pending" and initial["ai_attempts"] == 0
    await student_view()
    service = get_grading_service()
    original_grade = service.grade_short_answer
    calls = 0

    async def grade_once(question, answer_text):
        nonlocal calls
        if calls >= 1:
            raise RuntimeError("Live request budget exhausted")
        calls += 1
        return await original_grade(question, answer_text)

    service.grade_short_answer = grade_once
    task = asyncio.create_task(GradingWorker(test_settings, service).run())
    try:
        async with asyncio.timeout(70):
            while True:
                result = await request(client, "GET", path, headers=teacher)
                if result["ai_status"] in {"succeeded", "failed"}:
                    break
                if task.done():
                    await task
                    pytest.fail("Worker stopped unexpectedly")
                await asyncio.sleep(0.1)
        assert result["ai_status"] == "succeeded", result.get("ai_error_code")
        assert calls == 1 and result["ai_attempts"] == 1 and result["ai_retry_count"] == 0
        assert 0 <= result["ai_total_score"] <= 10
        assert result["answers"][0]["ai_comment"]
        assert result["final_total_score"] is None
        print(f"Live AI draft: score={result['ai_total_score']}; comment={result['answers'][0]['ai_comment']}")
        await student_view()
        grades = {"grades": [{"question_id": question["id"], "final_score": 10,
                              "final_comment": "Teacher reviewed: all criteria met."}]}
        confirmed = await request(client, "POST", path + "/confirm-grade", headers=teacher, json=grades)
        assert confirmed["final_total_score"] == 10
        await student_view(10)
        await request(client, "POST", path + "/confirm-grade", 409, teacher, json=grades)
        print("Live requests=1; retries=0; teacher confirmation and student score checks passed")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.aclose()
