import asyncio

import pytest
import pytest_asyncio
from beanie import PydanticObjectId

from app.models import (
    Assignment, ClassGroup, Question, QuestionSnapshot, Submission, UserRole,
)
from app.services.grading import GradeResult, get_grading_service

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def assignment_setup(accounts):
    teacher = accounts[UserRole.teacher]
    group = await ClassGroup(name="Snapshot class", code="SNAP01", teacher_id=teacher.id).insert()
    await accounts[UserRole.student].set({"class_id": group.id})
    questions = []
    for index in range(2):
        questions.append(await Question(
            prompt=f"Question {index}", reference_answer=f"SECRET_ANSWER_{index}",
            max_score=10 + index, rubric=f"SECRET_RUBRIC_{index}",
            image_urls=[f"http://testserver/uploads/{index}.png"], created_by=teacher.id,
        ).insert())
    return group, questions


async def create_work(api_client, headers, setup, **changes):
    group, questions = setup
    return await api_client.post("/api/v1/assignments", headers=headers, json={
        "title": "Snapshot work", "class_id": str(group.id),
        "questions": [{"question_id": str(q.id)} for q in reversed(questions)],
        **changes,
    })


@pytest.mark.parametrize("role", [UserRole.teacher, UserRole.admin])
async def test_publish_persists_complete_ordered_snapshots(
    api_client, auth_headers, assignment_setup, role,
):
    response = await create_work(api_client, await auth_headers(role), assignment_setup)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["question_source"] == "snapshot" and data["view"] == "teacher"
    original = list(reversed(assignment_setup[1]))
    stored = await Assignment.get(PydanticObjectId(data["id"]))
    assert stored.snapshot_version == 1
    for position, (item, source, response_item) in enumerate(
        zip(stored.questions, original, data["questions"], strict=True), 1,
    ):
        assert item.question_id == source.id
        assert item.snapshot == QuestionSnapshot.model_validate(source, from_attributes=True)
        assert response_item["position"] == position
        assert response_item["question_id"] == str(source.id)
        assert response_item["reference_answer"] == source.reference_answer
        assert response_item["rubric"] == source.rubric
        assert response_item["image_urls"] == source.image_urls
        assert (await Question.get(source.id)).content_locked


async def test_student_views_are_whitelisted_and_teacher_views_remain_private(
    api_client, auth_headers, assignment_setup,
):
    teacher = await auth_headers(UserRole.teacher)
    created = await create_work(api_client, teacher, assignment_setup)
    assignment_id = created.json()["id"]
    student = await auth_headers(UserRole.student)
    for path in ["/api/v1/assignments/my", f"/api/v1/assignments/{assignment_id}?view=teacher"]:
        response = await api_client.get(path, headers=student)
        assert response.status_code == 200
        assert "SECRET_" not in response.text
        assert "reference_answer" not in response.text and "rubric" not in response.text
        item = response.json()[0] if isinstance(response.json(), list) else response.json()
        assert item["view"] == "student" and item["question_source"] == "snapshot"
        assert [q["position"] for q in item["questions"]] == [1, 2]
        for question in item["questions"]:
            assert set(question) == {"question_id", "position", "prompt", "max_score", "image_urls"}
    for role in [UserRole.teacher, UserRole.admin]:
        for path in ["/api/v1/assignments/my", f"/api/v1/assignments/{assignment_id}"]:
            response = await api_client.get(path, headers=await auth_headers(role))
            assert response.status_code == 200 and "SECRET_ANSWER_" in response.text


@pytest.mark.parametrize("mutation", ["edit", "disable", "delete"])
async def test_snapshot_survives_source_changes_and_grading_uses_frozen_data(
    api_client, auth_headers, assignment_setup, test_app, mutation,
):
    teacher = await auth_headers(UserRole.teacher)
    student = await auth_headers(UserRole.student)
    created = await create_work(api_client, teacher, assignment_setup)
    assignment_id = created.json()["id"]
    before = (await api_client.get(f"/api/v1/assignments/{assignment_id}", headers=student)).json()
    for question in assignment_setup[1]:
        # Simulate a future maintenance path or external change; normal 07b writes remain locked.
        collection = Question.get_pymongo_collection()
        if mutation == "delete":
            await collection.delete_one({"_id": question.id})
        else:
            changes = {"is_active": False} if mutation == "disable" else {
                "prompt": "Changed", "reference_answer": "Changed", "rubric": "Changed",
                "image_urls": [], "max_score": 100,
            }
            await collection.update_one({"_id": question.id}, {"$set": changes})
    detail = await api_client.get(f"/api/v1/assignments/{assignment_id}", headers=student)
    assert detail.status_code == 200 and detail.json() == before
    seen = []

    class RecordingGrader:
        async def grade_short_answer(self, question, answer_text):
            assert isinstance(question, QuestionSnapshot)
            seen.append(question)
            return GradeResult(score=question.max_score, comment="Review required")

    test_app.dependency_overrides[get_grading_service] = lambda: RecordingGrader()
    answers = [{"question_id": str(q.id), "answer_text": "Example answer"} for q in assignment_setup[1]]
    submitted = await api_client.post(
        f"/api/v1/assignments/{assignment_id}/submissions", headers=student, json={"answers": answers},
    )
    assert submitted.status_code == 201, submitted.text
    assert "SECRET_" not in submitted.text and "ai_total_score" not in submitted.json()
    assert {q.reference_answer for q in seen} == {"SECRET_ANSWER_0", "SECRET_ANSWER_1"}
    assert {q.rubric for q in seen} == {"SECRET_RUBRIC_0", "SECRET_RUBRIC_1"}
    grades = [{"question_id": str(q.id), "final_score": q.max_score, "final_comment": "Reviewed"}
              for q in assignment_setup[1]]
    submission_id = submitted.json()["id"]
    bad_grades = [{**g, "final_score": 50} for g in grades]
    assert (await api_client.post(
        f"/api/v1/submissions/{submission_id}/confirm-grade", headers=teacher, json={"grades": bad_grades},
    )).status_code == 400
    confirmed = await api_client.post(
        f"/api/v1/submissions/{submission_id}/confirm-grade", headers=teacher, json={"grades": grades},
    )
    assert confirmed.status_code == 200 and confirmed.json()["final_total_score"] == 21
    assert (await Assignment.get(PydanticObjectId(assignment_id))).questions[0].snapshot.max_score == 11


async def test_draft_preview_is_teacher_only_and_not_a_publication_snapshot(
    api_client, auth_headers, assignment_setup,
):
    teacher = await auth_headers(UserRole.teacher)
    created = await create_work(api_client, teacher, assignment_setup, status="draft")
    assert created.status_code == 201
    assert created.json()["question_source"] == "draft_preview"
    assignment_id = created.json()["id"]
    stored = await Assignment.get(PydanticObjectId(assignment_id))
    assert stored.snapshot_version is None and all(q.snapshot is None for q in stored.questions)
    student = await auth_headers(UserRole.student)
    assert (await api_client.get("/api/v1/assignments/my", headers=student)).json() == []
    response = await api_client.get(f"/api/v1/assignments/{assignment_id}", headers=student)
    assert response.status_code == 404 and "SECRET_" not in response.text
    response = await api_client.post(
        f"/api/v1/assignments/{assignment_id}/submissions", headers=student,
        json={"answers": [{"question_id": str(q.id), "answer_text": "Answer"} for q in assignment_setup[1]]},
    )
    assert response.status_code == 404 and await Submission.count() == 0
    for role in [UserRole.teacher, UserRole.admin]:
        response = await api_client.get(f"/api/v1/assignments/{assignment_id}", headers=await auth_headers(role))
        assert response.status_code == 200 and response.json()["question_source"] == "draft_preview"


async def test_other_classes_and_teachers_cannot_read_contents(
    api_client, auth_headers, accounts, assignment_setup,
):
    created = await create_work(api_client, await auth_headers(UserRole.teacher), assignment_setup)
    assignment_id = created.json()["id"]
    other = await ClassGroup(name="Other", code="SNAP02", teacher_id=accounts[UserRole.admin].id).insert()
    await accounts[UserRole.student].set({"class_id": other.id})
    response = await api_client.get(
        f"/api/v1/assignments/{assignment_id}", headers=await auth_headers(UserRole.student),
    )
    assert response.status_code == 403 and "SECRET_" not in response.text
    assert (await api_client.get("/api/v1/assignments/my", headers=await auth_headers(UserRole.student))).json() == []
    await ClassGroup.get_pymongo_collection().update_one(
        {"_id": assignment_setup[0].id}, {"$set": {"teacher_id": accounts[UserRole.admin].id}},
    )
    response = await api_client.get(
        f"/api/v1/assignments/{assignment_id}", headers=await auth_headers(UserRole.teacher),
    )
    assert response.status_code == 403 and "SECRET_" not in response.text
    assert (await api_client.get("/api/v1/assignments/my", headers=await auth_headers(UserRole.teacher))).json() == []


@pytest.mark.parametrize("inactive", [False, True])
async def test_legacy_reference_is_explicit_without_silent_migration(
    api_client, auth_headers, accounts, assignment_setup, inactive,
):
    group, questions = assignment_setup
    work = await Assignment(
        title="Legacy", class_id=group.id, created_by=accounts[UserRole.teacher].id,
        questions=[{"question_id": q.id} for q in questions],
    ).insert()
    await Assignment.get_pymongo_collection().update_one(
        {"_id": work.id}, {"$unset": {"snapshot_version": "", "questions.0.snapshot": "", "questions.1.snapshot": ""}},
    )
    if inactive:
        await Question.get_pymongo_collection().update_many({}, {"$set": {"is_active": False}})
    before = await Assignment.get_pymongo_collection().find_one({"_id": work.id})
    for role in [UserRole.teacher, UserRole.student]:
        response = await api_client.get(f"/api/v1/assignments/{work.id}", headers=await auth_headers(role))
        assert response.status_code == 200 and response.json()["question_source"] == "legacy_reference"
        assert response.json()["questions"][0]["prompt"] == questions[0].prompt
        if role == UserRole.student:
            assert "SECRET_" not in response.text
    assert await Assignment.get_pymongo_collection().find_one({"_id": work.id}) == before
    result = await api_client.patch(
        f"/api/v1/questions/{questions[0].id}", headers=await auth_headers(UserRole.teacher),
        json={"prompt": "Forbidden edit"},
    )
    assert result.status_code == 409


@pytest.mark.parametrize("corruption", ["missing_item", "missing_all", "missing_version", "unsupported_version"])
async def test_incomplete_snapshot_never_falls_back_to_live_questions(
    api_client, auth_headers, assignment_setup, corruption,
):
    teacher = await auth_headers(UserRole.teacher)
    created = await create_work(api_client, teacher, assignment_setup)
    assignment_id = created.json()["id"]
    changes = {
        "missing_item": {"questions.0.snapshot": None},
        "missing_all": {"questions.0.snapshot": None, "questions.1.snapshot": None},
        "missing_version": {"snapshot_version": None},
        "unsupported_version": {"snapshot_version": 2},
    }[corruption]
    await Assignment.get_pymongo_collection().update_one(
        {"_id": PydanticObjectId(assignment_id)}, {"$set": changes},
    )
    student = await auth_headers(UserRole.student)
    for path in [f"/api/v1/assignments/{assignment_id}", "/api/v1/assignments/my"]:
        response = await api_client.get(path, headers=student)
        assert response.status_code == 409 and "SECRET_" not in response.text
    response = await api_client.post(
        f"/api/v1/assignments/{assignment_id}/submissions", headers=student,
        json={"answers": [{"question_id": str(q.id), "answer_text": "Answer"} for q in assignment_setup[1]]},
    )
    assert response.status_code == 409 and await Submission.count() == 0


async def test_missing_legacy_question_reports_repair_without_mutating_assignment(
    api_client, auth_headers, accounts, assignment_setup,
):
    group, questions = assignment_setup
    work = await Assignment(title="Legacy", class_id=group.id, created_by=accounts[UserRole.teacher].id,
                            questions=[{"question_id": q.id} for q in questions]).insert()
    await questions[0].delete()
    before = (await Assignment.get(work.id)).model_dump()
    response = await api_client.get(f"/api/v1/assignments/{work.id}", headers=await auth_headers(UserRole.teacher))
    assert response.status_code == 409
    assert (await Assignment.get(work.id)).model_dump() == before


async def test_snapshot_captures_edit_that_wins_before_lock(
    api_client, auth_headers, assignment_setup, monkeypatch,
):
    from app.api.routes import assignments as routes

    teacher = await auth_headers(UserRole.teacher)
    waiting, resume = asyncio.Event(), asyncio.Event()
    original = routes.lock_assignment_questions

    async def pause(ids, actor):
        waiting.set()
        await asyncio.wait_for(resume.wait(), 5)
        await original(ids, actor)

    monkeypatch.setattr(routes, "lock_assignment_questions", pause)
    task = asyncio.create_task(create_work(api_client, teacher, assignment_setup))
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        response = await api_client.patch(
            f"/api/v1/questions/{assignment_setup[1][0].id}", headers=teacher,
            json={"prompt": "Updated before lock", "reference_answer": "New answer", "max_score": 20},
        )
        assert response.status_code == 200
    finally:
        resume.set()
        result = await task
    assert result.status_code == 201
    captured = result.json()["questions"][1]
    assert captured["prompt"] == "Updated before lock"
    assert captured["reference_answer"] == "New answer" and captured["max_score"] == 20


@pytest.mark.parametrize("invalid", ["duplicate", "injected_snapshot", "injected_version"])
async def test_reject_ambiguous_or_client_controlled_snapshots(
    api_client, auth_headers, assignment_setup, invalid,
):
    question = {"question_id": str(assignment_setup[1][0].id)}
    changes = {}
    if invalid == "duplicate":
        changes["questions"] = [question, question]
    elif invalid == "injected_snapshot":
        changes["questions"] = [{**question, "snapshot": {"reference_answer": "Injected"}}]
    else:
        changes["snapshot_version"] = 1
    response = await create_work(api_client, await auth_headers(UserRole.teacher), assignment_setup, **changes)
    assert response.status_code == (400 if invalid == "duplicate" else 422)
    assert await Assignment.count() == 0
    assert not any([(await Question.get(q.id)).content_locked for q in assignment_setup[1]])


async def test_openapi_has_discriminated_role_responses(api_client):
    schema = (await api_client.get("/openapi.json")).json()
    models = schema["components"]["schemas"]
    assert "reference_answer" not in models["StudentAssignmentQuestionRead"]["properties"]
    assert "rubric" not in models["StudentAssignmentQuestionRead"]["properties"]
    assert "reference_answer" in models["TeacherAssignmentQuestionRead"]["properties"]
    response = schema["paths"]["/api/v1/assignments/{assignment_id}"]["get"]["responses"]
    assert {"401", "403", "404", "409", "422"} <= response.keys()
    assert response["200"]["content"]["application/json"]["schema"]["discriminator"]["propertyName"] == "view"


async def test_missing_snapshot_blocks_confirmation_without_changing_submission(
    api_client, auth_headers, assignment_setup,
):
    teacher = await auth_headers(UserRole.teacher)
    student = await auth_headers(UserRole.student)
    created = await create_work(api_client, teacher, assignment_setup)
    assignment_id = PydanticObjectId(created.json()["id"])
    submitted = await api_client.post(
        f"/api/v1/assignments/{assignment_id}/submissions", headers=student,
        json={"answers": [{"question_id": str(q.id), "answer_text": q.reference_answer}
                          for q in assignment_setup[1]]},
    )
    assert submitted.status_code == 201
    submission_id = PydanticObjectId(submitted.json()["id"])
    before = (await Submission.get(submission_id)).model_dump()
    await Assignment.get_pymongo_collection().update_one(
        {"_id": assignment_id}, {"$set": {"questions.0.snapshot": None}},
    )
    result = await api_client.post(
        f"/api/v1/submissions/{submission_id}/confirm-grade", headers=teacher,
        json={"grades": [{"question_id": str(q.id), "final_score": 5} for q in assignment_setup[1]]},
    )
    assert result.status_code == 409
    assert (await Submission.get(submission_id)).model_dump() == before
