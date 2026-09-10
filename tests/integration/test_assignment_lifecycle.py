import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from beanie import PydanticObjectId

from app.models import Assignment, ClassGroup, Question, Submission, UserRole
from app.services.grading import GradeResult, get_grading_service

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def work(api_client, auth_headers, accounts):
    teacher = await auth_headers(UserRole.teacher)
    student = await auth_headers(UserRole.student)
    group = await ClassGroup(name="Lifecycle class", code="LIFE01", teacher_id=accounts[UserRole.teacher].id).insert()
    await accounts[UserRole.student].set({"class_id": group.id})
    question = await Question(prompt="Explain", reference_answer="SECRET", max_score=10,
                              created_by=accounts[UserRole.teacher].id).insert()
    payload = {"title": "Draft", "class_id": str(group.id), "status": "draft",
               "questions": [{"question_id": str(question.id)}]}
    response = await api_client.post("/api/v1/assignments", headers=teacher, json=payload)
    assert response.status_code == 201, response.text
    return {
        "path": f"/api/v1/assignments/{response.json()['id']}", "teacher": teacher,
        "student": student, "group": group, "question": question, "payload": payload,
        "id": PydanticObjectId(response.json()["id"]),
        "answers": {"answers": [{"question_id": str(question.id), "answer_text": "Example"}]},
    }


async def test_draft_publish_archive_and_historical_grading(api_client, work):
    path, teacher, student = work["path"], work["teacher"], work["student"]
    patched = await api_client.patch(path, headers=teacher, json={"title": "  Edited  ", "description": "Note"})
    assert patched.status_code == 200 and patched.json()["title"] == "Edited"
    assert (await api_client.get(path, headers=student)).status_code == 404
    published = await api_client.post(path + "/publish", headers=teacher)
    assert published.status_code == 200 and published.json()["question_source"] == "snapshot"
    stored = await Assignment.get(work["id"])
    assert stored.snapshot_version == 1 and stored.revision == 2
    assert (await api_client.post(path + "/publish", headers=teacher)).json() == published.json()
    assert (await Assignment.get(work["id"])).revision == 2
    assert (await api_client.patch(path, headers=teacher, json={"due_at": None})).status_code == 409
    visible = await api_client.get(path, headers=student)
    assert visible.status_code == 200 and "SECRET" not in visible.text
    submitted = await api_client.post(path + "/submissions", headers=student, json=work["answers"])
    assert submitted.status_code == 201, submitted.text
    archived = await api_client.post(path + "/archive", headers=teacher)
    assert archived.status_code == 200 and archived.json()["question_source"] == "snapshot"
    assert archived.json()["questions"] == published.json()["questions"]
    assert (await api_client.post(path + "/archive", headers=teacher)).json() == archived.json()
    assert (await api_client.post(path + "/publish", headers=teacher)).status_code == 409
    assert (await api_client.patch(path, headers=teacher, json={"title": "No"})).status_code == 409
    assert (await api_client.get(path, headers=student)).status_code == 404
    assert (await api_client.post(path + "/submissions", headers=student, json=work["answers"])).status_code == 404
    submission_path = f"/api/v1/submissions/{submitted.json()['id']}"
    confirmed = await api_client.post(submission_path + "/confirm-grade", headers=teacher, json={
        "grades": [{"question_id": str(work["question"].id), "final_score": 8, "final_comment": "Reviewed"}],
    })
    assert confirmed.status_code == 200, confirmed.text
    assert (await api_client.get(submission_path, headers=student)).json()["final_total_score"] == 8


async def test_draft_question_replacement_validation_and_legacy_revision(api_client, work, accounts):
    path, teacher = work["path"], work["teacher"]
    for items in [[], work["payload"]["questions"] * 2, [{"question_id": str(PydanticObjectId())}]]:
        assert (await api_client.patch(path, headers=teacher, json={"questions": items})).status_code == 400
    question = await Question(prompt="New", reference_answer="Answer", max_score=5,
                              created_by=accounts[UserRole.admin].id).insert()
    replacement = {"questions": [{"question_id": str(question.id)}]}
    assert (await api_client.patch(path, headers=teacher, json=replacement)).status_code == 403
    await question.set({"created_by": accounts[UserRole.teacher].id, "is_active": False})
    assert (await api_client.patch(path, headers=teacher, json=replacement)).status_code == 400
    await question.set({"is_active": True})
    await Assignment.get_pymongo_collection().update_one({"_id": work["id"]}, {"$unset": {"revision": ""}})
    updated = await api_client.patch(path, headers=teacher, json=replacement)
    assert updated.status_code == 200 and updated.json()["questions"][0]["prompt"] == "New"
    assert (await Assignment.get(work["id"])).revision == 1
    assert (await Question.get(question.id)).content_locked
    assert (await Question.get(work["question"].id)).content_locked


async def test_visibility_filters_and_archived_draft(api_client, work, auth_headers, accounts):
    path, teacher, student = work["path"], work["teacher"], work["student"]
    for state in ["draft", "archived"]:
        if state == "archived":
            response = await api_client.post(path + "/archive", headers=teacher)
            assert response.status_code == 200 and response.json()["question_source"] == "draft_preview"
        for role_headers in [teacher, await auth_headers(UserRole.admin)]:
            result = await api_client.get(f"/api/v1/assignments/my?status={state}", headers=role_headers)
            assert [item["id"] for item in result.json()] == [str(work["id"])]
        assert (await api_client.get(f"/api/v1/assignments/my?status={state}", headers=student)).json() == []
    published = await api_client.post("/api/v1/assignments", headers=teacher, json={**work["payload"], "status": "published"})
    assert published.status_code == 201
    assert len((await api_client.get("/api/v1/assignments/my?status=published", headers=student)).json()) == 1
    await accounts[UserRole.student].set({"class_id": PydanticObjectId()})
    assert (await api_client.get("/api/v1/assignments/my", headers=student)).json() == []
    assert (await api_client.get(f"/api/v1/assignments/{published.json()['id']}", headers=student)).status_code == 403


async def test_mutation_authorization_and_archived_class(api_client, work, auth_headers, accounts):
    calls = [("PATCH", work["path"], {"title": "Edited"}),
             ("POST", work["path"] + "/publish", None), ("POST", work["path"] + "/archive", None)]
    for method, path, payload in calls:
        assert (await api_client.request(method, path, json=payload)).status_code == 401
        assert (await api_client.request(method, path, json=payload, headers=work["student"])).status_code == 403
    await work["group"].set({"teacher_id": accounts[UserRole.admin].id})
    for method, path, payload in calls:
        assert (await api_client.request(method, path, json=payload, headers=work["teacher"])).status_code == 403
    admin = await auth_headers(UserRole.admin)
    assert (await api_client.patch(work["path"], json={"title": "Admin edit"}, headers=admin)).status_code == 200
    await work["group"].set({"is_active": False})
    for method, path, payload in calls[:2]:
        assert (await api_client.request(method, path, json=payload, headers=admin)).status_code == 409
    assert (await api_client.post(work["path"] + "/archive", headers=admin)).status_code == 200


async def test_deadline_roundtrip_boundary_and_expired_draft(api_client, work, monkeypatch):
    import app.services.assignments as service

    now = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(service, "utc_now", lambda: now)
    teacher, path = work["teacher"], work["path"]
    expired = {**work["payload"], "status": "published", "due_at": now.isoformat()}
    assert (await api_client.post("/api/v1/assignments", headers=teacher, json=expired)).status_code == 400
    assert (await api_client.patch(path, headers=teacher, json={"due_at": now.isoformat()})).status_code == 200
    assert (await api_client.post(path + "/publish", headers=teacher)).status_code == 400
    assert (await Assignment.get(work["id"])).snapshot_version is None
    await api_client.patch(path, headers=teacher, json={"due_at": None, "description": None})
    assert (await Assignment.get(work["id"])).due_at is None
    await api_client.patch(path, headers=teacher, json={"due_at": "2030-01-01T08:00:01+08:00"})
    published = await api_client.post(path + "/publish", headers=teacher)
    assert published.status_code == 200 and published.json()["due_at"] == "2030-01-01T00:00:01Z"
    accepted = await api_client.post(path + "/submissions", headers=work["student"], json=work["answers"])
    assert accepted.status_code == 201, accepted.text
    monkeypatch.setattr(service, "utc_now", lambda: now + timedelta(seconds=1))
    assert (await api_client.get(path, headers=work["student"])).status_code == 200
    closed = await api_client.post(path + "/submissions", headers=work["student"], json=work["answers"])
    assert closed.status_code == 400 and closed.json()["detail"] == "Assignment is closed"
    assert await Submission.count() == 1


@pytest.mark.parametrize("competing_change", ["edit", "archive"])
async def test_publish_does_not_overwrite_concurrent_change(api_client, work, monkeypatch, competing_change):
    import app.api.routes.assignments as routes

    entered, release = asyncio.Event(), asyncio.Event()
    original = routes.update_assignment_atomically

    async def pause_publish(assignment, changes):
        if changes.get("status") == "published":
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return await original(assignment, changes)

    monkeypatch.setattr(routes, "update_assignment_atomically", pause_publish)
    task = asyncio.create_task(api_client.post(work["path"] + "/publish", headers=work["teacher"]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if competing_change == "edit":
            response = await api_client.patch(work["path"], headers=work["teacher"], json={"title": "Winner"})
        else:
            response = await api_client.post(work["path"] + "/archive", headers=work["teacher"])
        assert response.status_code == 200
    finally:
        release.set()
        result = await task
    assert result.status_code == 409
    stored = await Assignment.get(work["id"])
    assert stored.snapshot_version is None and stored.revision == 1
    if competing_change == "edit":
        assert stored.title == "Winner" and stored.status == "draft"
    else:
        assert stored.status == "archived"


@pytest.mark.parametrize("change", ["archive", "deadline"])
async def test_admission_rechecked_after_grading(api_client, work, test_app, monkeypatch, change):
    import app.services.assignments as service

    now = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(service, "utc_now", lambda: now)
    await api_client.patch(work["path"], headers=work["teacher"], json={"due_at": (now + timedelta(seconds=1)).isoformat()})
    assert (await api_client.post(work["path"] + "/publish", headers=work["teacher"])).status_code == 200

    class SlowGrader:
        async def grade_short_answer(self, question, answer_text):
            if change == "archive":
                assert (await api_client.post(work["path"] + "/archive", headers=work["teacher"])).status_code == 200
            else:
                monkeypatch.setattr(service, "utc_now", lambda: now + timedelta(seconds=1))
            return GradeResult(score=1, comment="Review")

    test_app.dependency_overrides[get_grading_service] = lambda: SlowGrader()
    result = await api_client.post(work["path"] + "/submissions", headers=work["student"], json=work["answers"])
    assert result.status_code == (404 if change == "archive" else 400)
    assert await Submission.count() == 0
