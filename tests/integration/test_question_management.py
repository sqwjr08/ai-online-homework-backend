import asyncio
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from beanie import PydanticObjectId

from app.models import Assignment, AssignmentStatus, ClassGroup, Question, UserRole
from app.services import questions as service

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def editable_question(accounts):
    return await Question(
        prompt="Original prompt", reference_answer="Original answer", max_score=10,
        rubric="Original rubric", image_urls=["http://testserver/uploads/original.png"],
        created_by=accounts[UserRole.teacher].id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    ).insert()


@pytest_asyncio.fixture
async def assignment_payload(accounts, editable_question):
    group = await ClassGroup(
        name="Example class", code="ABCD12", teacher_id=accounts[UserRole.teacher].id,
    ).insert()
    return {
        "title": "Example work", "class_id": str(group.id),
        "questions": [{"question_id": str(editable_question.id)}],
    }


async def write_question(client, headers, question_id, operation, payload=None):
    if operation == "edit":
        return await client.patch(
            f"/api/v1/questions/{question_id}", headers=headers,
            json=payload if payload is not None else {"prompt": "Changed prompt"},
        )
    return await client.post(f"/api/v1/questions/{question_id}/disable", headers=headers)


@pytest.mark.parametrize("actor", [UserRole.teacher, UserRole.admin])
async def test_partial_edit_preserves_other_fields(api_client, auth_headers, editable_question, actor):
    before = await Question.get(editable_question.id)
    result = await write_question(
        api_client, await auth_headers(actor), before.id, "edit", {"prompt": " New prompt "},
    )
    assert result.status_code == 200, result.text
    assert result.json()["prompt"] == "New prompt"
    after = await Question.get(before.id)
    for field in ("reference_answer", "rubric", "max_score", "image_urls", "created_by", "created_at"):
        assert getattr(after, field) == getattr(before, field)
    assert after.updated_at > before.updated_at
    assert after.is_active and not after.content_locked


async def test_edit_all_fields_and_clear_optional_values(api_client, auth_headers, editable_question):
    headers = await auth_headers(UserRole.teacher)
    payload = {
        "prompt": " New ", "reference_answer": " Answer ", "max_score": 2.5,
        "rubric": " Rule ", "image_urls": ["http://testserver/uploads/new.png"],
    }
    result = await write_question(api_client, headers, editable_question.id, "edit", payload)
    assert result.status_code == 200
    assert result.json()["reference_answer"] == "Answer"
    assert result.json()["rubric"] == "Rule"
    assert result.json()["max_score"] == 2.5
    assert result.json()["image_urls"] == payload["image_urls"]
    result = await write_question(
        api_client, headers, editable_question.id, "edit", {"rubric": None, "image_urls": []},
    )
    assert result.status_code == 200
    assert result.json()["rubric"] is None and result.json()["image_urls"] == []
    assert result.json()["prompt"] == "New"


@pytest.mark.parametrize("operation", ["edit", "disable"])
@pytest.mark.parametrize("actor", [None, UserRole.student, "foreign_teacher"])
async def test_writes_require_owner_or_admin(
    api_client, auth_headers, accounts, editable_question, operation, actor,
):
    if actor == "foreign_teacher":
        await Question.get_pymongo_collection().update_one(
            {"_id": editable_question.id}, {"$set": {"created_by": accounts[UserRole.admin].id}},
        )
        headers = await auth_headers(UserRole.teacher)
    else:
        headers = await auth_headers(actor) if actor else {}
    before = (await Question.get(editable_question.id)).model_dump()
    response = await write_question(api_client, headers, editable_question.id, operation)
    assert response.status_code == (401 if actor is None else 403)
    assert (await Question.get(editable_question.id)).model_dump() == before


@pytest.mark.parametrize("operation", ["edit", "disable"])
@pytest.mark.parametrize("target,expected", [("bad-id", 422), ("507f1f77bcf86cd799439011", 404)])
async def test_write_target_errors(api_client, auth_headers, operation, target, expected):
    response = await write_question(api_client, await auth_headers(UserRole.admin), target, operation)
    assert response.status_code == expected


@pytest.mark.parametrize(
    "payload",
    [{}, {"prompt": None}, {"max_score": None}, {"image_urls": None}, {"rubric": " "},
     {"reference_answer": "\n"}, {"max_score": "NaN"}, {"max_score": True},
     {"content_locked": False}, {"is_active": False}, {"created_by": "injected"}],
)
async def test_invalid_edit_does_not_write(api_client, auth_headers, editable_question, payload):
    before = (await Question.get(editable_question.id)).model_dump()
    response = await write_question(
        api_client, await auth_headers(UserRole.teacher), editable_question.id, "edit", payload,
    )
    assert response.status_code == 422
    assert (await Question.get(editable_question.id)).model_dump() == before


@pytest.mark.parametrize("actor", [UserRole.teacher, UserRole.admin])
async def test_disable_is_idempotent_and_cannot_edit_or_assign(
    api_client, auth_headers, editable_question, assignment_payload, actor,
):
    headers = await auth_headers(actor)
    responses = await asyncio.gather(*[
        write_question(api_client, headers, editable_question.id, "disable") for _ in range(2)
    ])
    assert all(r.status_code == 200 and not r.json()["is_active"] for r in responses)
    before = (await Question.get(editable_question.id)).model_dump()
    assert (await write_question(api_client, headers, editable_question.id, "disable")).status_code == 200
    assert (await Question.get(editable_question.id)).model_dump() == before
    assert (await write_question(api_client, headers, editable_question.id, "edit")).status_code == 409
    assert (await api_client.get("/api/v1/questions", headers=headers)).json()["total"] == 0
    disabled = await api_client.get("/api/v1/questions", headers=headers, params={"is_active": False})
    assert disabled.json()["total"] == 1
    assert (await api_client.get(f"/api/v1/questions/{editable_question.id}", headers=headers)).status_code == 200
    response = await api_client.post("/api/v1/assignments", headers=headers, json=assignment_payload)
    assert response.status_code == 400
    assert await Assignment.count() == 0


@pytest.mark.parametrize("status", [AssignmentStatus.draft, AssignmentStatus.published])
@pytest.mark.parametrize("operation", ["edit", "disable"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_referenced_questions_are_immutable(
    api_client, auth_headers, accounts, editable_question, assignment_payload, status, operation, legacy,
):
    headers = await auth_headers(UserRole.teacher)
    if legacy:
        await Question.get_pymongo_collection().update_one(
            {"_id": editable_question.id}, {"$unset": {"content_locked": ""}},
        )
        await Assignment(
            title="Legacy", class_id=PydanticObjectId(assignment_payload["class_id"]),
            questions=[{"question_id": editable_question.id}], status=status,
            created_by=accounts[UserRole.teacher].id,
        ).insert()
    else:
        result = await api_client.post(
            "/api/v1/assignments", headers=headers, json={**assignment_payload, "status": status},
        )
        assert result.status_code == 201
    original = (await Question.get(editable_question.id)).model_dump(exclude={"content_locked"})
    assignments = [a.model_dump() for a in await Assignment.find_all().to_list()]
    for role in [UserRole.teacher, UserRole.admin]:
        result = await write_question(api_client, await auth_headers(role), editable_question.id, operation)
        assert result.status_code == 409
    current = await Question.get(editable_question.id)
    assert current.content_locked
    assert current.model_dump(exclude={"content_locked"}) == original
    assert [a.model_dump() for a in await Assignment.find_all().to_list()] == assignments
    # Reusing the immutable question in another assignment is still supported.
    result = await api_client.post("/api/v1/assignments", headers=headers, json=assignment_payload)
    assert result.status_code == 201


async def test_unreferenced_legacy_question_can_be_edited(api_client, auth_headers, editable_question):
    await Question.get_pymongo_collection().update_one(
        {"_id": editable_question.id}, {"$unset": {"content_locked": ""}},
    )
    result = await write_question(
        api_client, await auth_headers(UserRole.teacher), editable_question.id, "edit",
    )
    assert result.status_code == 200


@pytest.mark.parametrize("operation", ["edit", "disable"])
async def test_assignment_lock_wins_after_reference_check(
    api_client, auth_headers, editable_question, assignment_payload, monkeypatch, operation,
):
    headers = await auth_headers(UserRole.teacher)
    checked, resume = asyncio.Event(), asyncio.Event()
    original = service.protect_legacy_reference

    async def pause_after_check(question):
        await original(question)
        checked.set()
        await asyncio.wait_for(resume.wait(), 5)

    monkeypatch.setattr(service, "protect_legacy_reference", pause_after_check)
    write = asyncio.create_task(write_question(api_client, headers, editable_question.id, operation))
    try:
        await asyncio.wait_for(checked.wait(), 5)
        response = await api_client.post("/api/v1/assignments", headers=headers, json=assignment_payload)
        assert response.status_code == 201
    finally:
        resume.set()
        result = await write
    assert result.status_code == 409
    current = await Question.get(editable_question.id)
    assert current.prompt == "Original prompt" and current.is_active and current.content_locked


@pytest.mark.parametrize("operation", ["edit", "disable"])
async def test_question_write_wins_before_assignment_lock(
    api_client, auth_headers, editable_question, assignment_payload, monkeypatch, operation,
):
    from app.api.routes import assignments as routes

    headers = await auth_headers(UserRole.teacher)
    checked, resume = asyncio.Event(), asyncio.Event()
    original = routes.lock_assignment_questions

    async def pause_before_lock(ids, actor):
        checked.set()
        await asyncio.wait_for(resume.wait(), 5)
        await original(ids, actor)

    monkeypatch.setattr(routes, "lock_assignment_questions", pause_before_lock)
    create = asyncio.create_task(api_client.post(
        "/api/v1/assignments", headers=headers, json=assignment_payload,
    ))
    try:
        await asyncio.wait_for(checked.wait(), 5)
        result = await write_question(api_client, headers, editable_question.id, operation)
        assert result.status_code == 200
    finally:
        resume.set()
        response = await create
    assert response.status_code == (201 if operation == "edit" else 409)
    current = await Question.get(editable_question.id)
    if operation == "edit":
        assert current.prompt == "Changed prompt" and current.content_locked
    else:
        assert not current.is_active and await Assignment.count() == 0


async def test_failed_assignment_keeps_lock_without_unsafe_rollback(
    api_client, auth_headers, editable_question, assignment_payload, monkeypatch,
):
    async def fail_insert(self, *args, **kwargs):
        raise RuntimeError("Simulated storage failure")

    monkeypatch.setattr(Assignment, "insert", fail_insert)
    headers = await auth_headers(UserRole.teacher)
    with pytest.raises(RuntimeError, match="Simulated storage failure"):
        await api_client.post("/api/v1/assignments", headers=headers, json=assignment_payload)
    assert await Assignment.count() == 0
    assert (await Question.get(editable_question.id)).content_locked
    assert (await write_question(api_client, headers, editable_question.id, "edit")).status_code == 409


async def test_concurrent_partial_edits_preserve_independent_fields(
    api_client, auth_headers, editable_question,
):
    headers = await auth_headers(UserRole.teacher)
    responses = await asyncio.gather(
        write_question(api_client, headers, editable_question.id, "edit", {"prompt": "New prompt"}),
        write_question(api_client, headers, editable_question.id, "edit", {"rubric": "New rubric"}),
    )
    assert all(r.status_code == 200 for r in responses)
    current = await Question.get(editable_question.id)
    assert current.prompt == "New prompt" and current.rubric == "New rubric"
    assert current.reference_answer == "Original answer" and current.max_score == 10


async def test_management_openapi(api_client):
    schema = (await api_client.get("/openapi.json")).json()
    for path, method in [("/api/v1/questions/{question_id}", "patch"),
                         ("/api/v1/questions/{question_id}/disable", "post")]:
        assert {"200", "401", "403", "404", "409", "422"} <= schema["paths"][path][method]["responses"].keys()
    update = schema["components"]["schemas"]["QuestionUpdate"]
    assert update["additionalProperties"] is False
    assert "content_locked" not in update["properties"]
    assert "409" in schema["paths"]["/api/v1/assignments"]["post"]["responses"]


async def test_locked_question_still_supports_submission_and_review(
    api_client, auth_headers, editable_question, assignment_payload,
):
    teacher = await auth_headers(UserRole.teacher)
    student = await auth_headers(UserRole.student)
    joined = await api_client.post("/api/v1/classes/join", headers=student, json={"code": "ABCD12"})
    assert joined.status_code == 200
    created = await api_client.post("/api/v1/assignments", headers=teacher, json=assignment_payload)
    assert created.status_code == 201
    assert (await write_question(api_client, teacher, editable_question.id, "disable")).status_code == 409
    assert (await write_question(api_client, teacher, editable_question.id, "edit")).status_code == 409
    submitted = await api_client.post(
        f"/api/v1/assignments/{created.json()['id']}/submissions", headers=student,
        json={"answers": [{"question_id": str(editable_question.id), "answer_text": "Original answer"}]},
    )
    assert submitted.status_code == 201
    assert submitted.json()["final_total_score"] is None
    submission_id = submitted.json()["id"]
    confirmed = await api_client.post(
        f"/api/v1/submissions/{submission_id}/confirm-grade", headers=teacher,
        json={"grades": [{"question_id": str(editable_question.id), "final_score": 10,
                          "final_comment": "Reviewed"}]},
    )
    assert confirmed.status_code == 200
    result = await api_client.get(f"/api/v1/submissions/{submission_id}", headers=student)
    assert result.status_code == 200 and result.json()["final_total_score"] == 10


async def test_partial_lock_failure_is_conservative(
    api_client, auth_headers, editable_question, assignment_payload, monkeypatch,
):
    from app.api.routes import assignments as routes

    second = await Question(
        prompt="Second", reference_answer="Answer", max_score=10,
        created_by=editable_question.created_by,
    ).insert()
    original = routes.lock_assignment_questions

    async def disable_second_before_lock(ids, actor):
        await Question.get_pymongo_collection().update_one(
            {"_id": second.id}, {"$set": {"is_active": False}},
        )
        await original(ids, actor)

    monkeypatch.setattr(routes, "lock_assignment_questions", disable_second_before_lock)
    payload = {**assignment_payload, "questions": assignment_payload["questions"] + [
        {"question_id": str(second.id)}
    ]}
    response = await api_client.post(
        "/api/v1/assignments", headers=await auth_headers(UserRole.teacher), json=payload,
    )
    assert response.status_code == 409 and await Assignment.count() == 0
    assert (await Question.get(editable_question.id)).content_locked
    assert not (await Question.get(second.id)).content_locked
