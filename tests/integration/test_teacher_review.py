import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from beanie import PydanticObjectId

from app.models import Assignment, ClassGroup, Question, Submission, UserRole

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def review_work(api_client, accounts, auth_headers):
    teacher = await auth_headers(UserRole.teacher)
    student = await auth_headers(UserRole.student)
    group = await ClassGroup(name="Review class", code="REV001", teacher_id=accounts[UserRole.teacher].id).insert()
    await accounts[UserRole.student].set({"class_id": group.id})
    questions = [await Question(prompt=f"Explain {i}", reference_answer="Answer", max_score=10,
                                created_by=accounts[UserRole.teacher].id).insert() for i in range(2)]
    result = await api_client.post("/api/v1/assignments", headers=teacher, json={
        "title": "Review work", "class_id": str(group.id),
        "questions": [{"question_id": str(q.id)} for q in questions],
    })
    assert result.status_code == 201, result.text
    path = f"/api/v1/assignments/{result.json()['id']}"
    created = await api_client.post(path + "/submissions", headers=student, json={
        "answers": [{"question_id": str(q.id), "answer_text": "Example"} for q in questions],
    })
    assert created.status_code == 201, created.text
    return {"path": path, "assignment_id": PydanticObjectId(result.json()["id"]),
            "id": PydanticObjectId(created.json()["id"]), "teacher": teacher, "student": student,
            "group": group, "questions": questions, "created": created.json(),
            "confirm": f"/api/v1/submissions/{created.json()['id']}/confirm-grade",
            "grades": [{"question_id": str(q.id), "final_score": score}
                       for q, score in zip(questions, [0.1, 0.2], strict=True)]}


def assert_student_view(data, confirmed):
    assert data["view"] == "student" and "ai_total_score" not in data
    assert all("ai_score" not in a and "ai_comment" not in a for a in data["answers"])
    assert data["final_total_score"] == (0.3 if confirmed else None)
    if not confirmed:
        assert data["reviewed_at"] is None and data["reviewed_by"] is None
        assert all(a["final_score"] is None and a["final_comment"] is None for a in data["answers"])


async def test_student_whitelist_and_first_confirmation_locks(api_client, review_work, auth_headers):
    work = review_work
    assert_student_view(work["created"], False)
    # Even stale private/final fields in a pending document must stay hidden.
    await Submission.get_pymongo_collection().update_one({"_id": work["id"]}, {"$set": {
        "answers.0.ai_comment": "PRIVATE_AI", "answers.0.final_comment": "UNCONFIRMED",
        "final_total_score": 99, "reviewed_by": PydanticObjectId(), "reviewed_at": datetime.now(UTC),
    }})
    paths = [work["path"] + "/submissions/my", f"/api/v1/submissions/{work['id']}?view=teacher"]
    for path in paths:
        pending = await api_client.get(path, headers=work["student"])
        assert pending.status_code == 200
        assert_student_view(pending.json(), False)
        assert "PRIVATE_AI" not in pending.text and "UNCONFIRMED" not in pending.text
    for role in [UserRole.teacher, UserRole.admin]:
        teacher_view = await api_client.get(paths[1], headers=await auth_headers(role))
        assert teacher_view.status_code == 200 and "PRIVATE_AI" in teacher_view.text
    confirmed = await api_client.post(work["confirm"], headers=work["teacher"], json={"grades": work["grades"]})
    assert confirmed.status_code == 200 and confirmed.json()["final_total_score"] == 0.3
    assert all(a["final_comment"] is None for a in confirmed.json()["answers"])
    before = (await Submission.get(work["id"])).model_dump()
    for role in [UserRole.teacher, UserRole.admin]:
        repeated = await api_client.post(work["confirm"], headers=await auth_headers(role), json={"grades": work["grades"]})
        assert repeated.status_code == 409
        assert (await Submission.get(work["id"])).model_dump() == before
    for path in paths:
        response = await api_client.get(path, headers=work["student"])
        assert_student_view(response.json(), True)
        assert "PRIVATE_AI" not in response.text


async def test_invalid_grades_do_not_partially_write(api_client, review_work):
    work = review_work
    before = (await Submission.get(work["id"])).model_dump()
    for grades, code in [
        ([], 422), (work["grades"][:1], 400),
        (work["grades"] + work["grades"][:1], 400),
        ([work["grades"][0], {**work["grades"][1], "question_id": str(PydanticObjectId())}], 400),
        ([work["grades"][0], {**work["grades"][1], "final_score": 10.01}], 400),
        ([{**work["grades"][0], "final_score": 0.001}, work["grades"][1]], 422),
    ]:
        result = await api_client.post(work["confirm"], headers=work["teacher"], json={"grades": grades})
        assert result.status_code == code, result.text
        assert (await Submission.get(work["id"])).model_dump() == before
    # Stored answer/question inconsistencies must fail closed rather than confirm partial work.
    await Submission.get_pymongo_collection().update_one({"_id": work["id"]}, {"$pop": {"answers": 1}})
    malformed = (await Submission.get(work["id"])).model_dump()
    response = await api_client.post(work["confirm"], headers=work["teacher"], json={"grades": work["grades"][:1]})
    assert response.status_code == 409
    assert (await Submission.get(work["id"])).model_dump() == malformed


async def test_concurrent_confirmation_has_one_winner(api_client, review_work, monkeypatch, auth_headers):
    import app.services.submissions as service

    work = review_work
    entered = 0
    ready = asyncio.Event()
    original = service.assignment_content

    async def barrier(assignment):
        nonlocal entered
        contents = await original(assignment)
        entered += 1
        if entered == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 5)
        return contents

    monkeypatch.setattr(service, "assignment_content", barrier)
    admin = await auth_headers(UserRole.admin)
    requests = [(work["teacher"], work["grades"]),
                (admin, [{**grade, "final_score": 5, "final_comment": "Admin review"} for grade in work["grades"]])]
    results = await asyncio.gather(*[
        api_client.post(work["confirm"], headers=headers, json={"grades": grades}) for headers, grades in requests
    ])
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    stored = await api_client.get(f"/api/v1/submissions/{work['id']}", headers=work["teacher"])
    assert stored.json() == winner


async def test_submission_list_pagination_filter_and_progress(api_client, review_work):
    work = review_work
    original = await Submission.get(work["id"])
    for _ in range(2):
        await Submission(assignment_id=work["assignment_id"], student_id=PydanticObjectId(),
                         answers=original.answers).insert()
    collection = Submission.get_pymongo_collection()
    await collection.update_many({}, {"$set": {"submitted_at": datetime(2030, 1, 1, tzinfo=UTC)}})
    await Submission(assignment_id=PydanticObjectId(), student_id=PydanticObjectId(), answers=[]).insert()
    path = work["path"] + "/submissions"
    empty = (await api_client.get(path + "?status=confirmed", headers=work["teacher"])).json()
    assert empty["items"] == [] and empty["total"] == 0
    assert empty["progress"] == {"submitted_count": 3, "pending_count": 3, "confirmed_count": 0}
    await api_client.post(work["confirm"], headers=work["teacher"], json={"grades": work["grades"]})
    ids = []
    for page in range(1, 4):
        result = await api_client.get(path + f"?page={page}&page_size=1", headers=work["teacher"])
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["page"] == page and data["page_size"] == 1 and data["total"] == 3
        assert data["progress"] == {"submitted_count": 3, "pending_count": 2, "confirmed_count": 1}
        assert data["items"][0]["view"] == "teacher"
        ids.append(data["items"][0]["id"])
    assert len(set(ids)) == 3 and ids == sorted(ids)
    pending = (await api_client.get(path + "?status=pending_teacher_review", headers=work["teacher"])).json()
    assert pending["total"] == 2 and pending["progress"]["submitted_count"] == 3
    assert (await api_client.get(path + "?page=9", headers=work["teacher"])).json()["items"] == []
    for query in ["page=0", "page_size=101", "status=invalid"]:
        assert (await api_client.get(path + "?" + query, headers=work["teacher"])).status_code == 422


async def test_review_permissions_and_archived_history(api_client, review_work, accounts, auth_headers):
    work = review_work
    list_path = work["path"] + "/submissions"
    for headers, code in [({}, 401), (work["student"], 403)]:
        assert (await api_client.get(list_path, headers=headers)).status_code == code
        assert (await api_client.post(work["confirm"], headers=headers, json={"grades": work["grades"]})).status_code == code
    await work["group"].set({"teacher_id": accounts[UserRole.admin].id})
    assert (await api_client.get(list_path, headers=work["teacher"])).status_code == 403
    assert (await api_client.post(work["confirm"], headers=work["teacher"], json={"grades": work["grades"]})).status_code == 403
    admin = await auth_headers(UserRole.admin)
    await api_client.post(work["path"] + "/archive", headers=admin)
    await work["group"].set({"is_active": False})
    assert (await api_client.get(list_path, headers=admin)).status_code == 200
    assert (await api_client.post(work["confirm"], headers=admin, json={"grades": work["grades"]})).status_code == 200
    assert (await api_client.get(f"/api/v1/assignments/{PydanticObjectId()}/submissions", headers=admin)).status_code == 404


async def test_nonfinite_total_is_rejected_without_write(api_client, review_work):
    work = review_work
    await Assignment.get_pymongo_collection().update_one({"_id": work["assignment_id"]}, {"$set": {
        "questions.0.snapshot.max_score": 1e308, "questions.1.snapshot.max_score": 1e308,
    }})
    before = (await Submission.get(work["id"])).model_dump()
    response = await api_client.post(work["confirm"], headers=work["teacher"], json={
        "grades": [{**g, "final_score": 1e308} for g in work["grades"]],
    })
    assert response.status_code == 400
    assert (await Submission.get(work["id"])).model_dump() == before


async def test_review_openapi_exposes_role_whitelists_and_locked_conflict(api_client):
    schema = (await api_client.get("/openapi.json")).json()
    models = schema["components"]["schemas"]
    assert "ai_total_score" not in models["StudentSubmissionRead"]["properties"]
    assert "ai_comment" not in models["StudentSubmissionAnswerRead"]["properties"]
    assert models["StudentSubmissionRead"]["additionalProperties"] is False
    detail = schema["paths"]["/api/v1/submissions/{submission_id}"]["get"]
    assert detail["responses"]["200"]["content"]["application/json"]["schema"]["discriminator"]["propertyName"] == "view"
    assert "409" in schema["paths"]["/api/v1/submissions/{submission_id}/confirm-grade"]["post"]["responses"]
    assert "get" in schema["paths"]["/api/v1/assignments/{assignment_id}/submissions"]
