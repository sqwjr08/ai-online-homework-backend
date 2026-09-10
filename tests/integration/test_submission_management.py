import asyncio

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.db import check_submission_index_readiness
from app.models import Assignment, ClassGroup, Question, Submission, UserRole
from app.services.grading import GradeResult, get_grading_service

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def submission_work(api_client, accounts, auth_headers):
    teacher = await auth_headers(UserRole.teacher)
    student = await auth_headers(UserRole.student)
    group = await ClassGroup(name="Submission class", code="SUB001", teacher_id=accounts[UserRole.teacher].id).insert()
    await accounts[UserRole.student].set({"class_id": group.id})
    question = await Question(prompt="Explain", reference_answer="Example", max_score=10,
                              created_by=accounts[UserRole.teacher].id).insert()
    response = await api_client.post("/api/v1/assignments", headers=teacher, json={
        "title": "Submission work", "class_id": str(group.id),
        "questions": [{"question_id": str(question.id)}],
    })
    assert response.status_code == 201, response.text
    return {
        "path": f"/api/v1/assignments/{response.json()['id']}",
        "id": PydanticObjectId(response.json()["id"]), "group": group,
        "teacher": teacher, "student": student, "question": question,
        "payload": {"answers": [{"question_id": str(question.id), "answer_text": "Example"}]},
    }


async def test_concurrent_submissions_have_one_winner(api_client, submission_work, test_app):
    work = submission_work
    both_grading = asyncio.Event()
    calls = 0

    class BarrierGrader:
        async def grade_short_answer(self, question, answer_text):
            nonlocal calls
            calls += 1
            if calls == 2:
                both_grading.set()
            await asyncio.wait_for(both_grading.wait(), 5)
            return GradeResult(score=7, comment="Draft feedback")

    test_app.dependency_overrides[get_grading_service] = lambda: BarrierGrader()
    results = await asyncio.gather(*[
        api_client.post(work["path"] + "/submissions", headers=work["student"], json=work["payload"])
        for _ in range(2)
    ])
    assert sorted(r.status_code for r in results) == [201, 409]
    stored = await Submission.find_one({"assignment_id": work["id"]})
    assert await Submission.count() == 1 and stored.ai_total_score == 7
    repeated = await api_client.post(work["path"] + "/submissions", headers=work["student"], json=work["payload"])
    assert repeated.status_code == 409 and calls == 2
    found = await api_client.get(work["path"] + "/submissions/my", headers=work["student"])
    assert found.status_code == 200 and found.json()["id"] == str(stored.id)
    assert "ai_total_score" not in found.json() and "ai_comment" not in found.json()["answers"][0]


async def test_answer_matching_rejected_before_grading(api_client, submission_work, test_app):
    work = submission_work

    class UnexpectedGrader:
        async def grade_short_answer(self, *args):
            pytest.fail("Invalid input must not reach grading")

    test_app.dependency_overrides[get_grading_service] = lambda: UnexpectedGrader()
    answer = work["payload"]["answers"][0]
    for payload, code in [
        ({"answers": []}, 422),
        ({"answers": [{**answer, "answer_text": " \n "}]}, 422),
        ({"answers": [answer, answer]}, 400),
        ({"answers": [{**answer, "question_id": str(PydanticObjectId())}]}, 400),
        ({"answers": [answer, {**answer, "question_id": str(PydanticObjectId())}]}, 400),
        ({"answers": [answer], "final_total_score": 10}, 422),
    ]:
        result = await api_client.post(work["path"] + "/submissions", headers=work["student"], json=payload)
        assert result.status_code == code, result.text
    assert await Submission.count() == 0
    collection = Assignment.get_pymongo_collection()
    await collection.update_one({"_id": work["id"]}, {"$push": {"questions": {"question_id": PydanticObjectId()}}})
    missing = await api_client.post(work["path"] + "/submissions", headers=work["student"], json=work["payload"])
    assert missing.status_code == 400


async def test_find_own_submission_after_archive_and_confirm(api_client, submission_work, auth_headers, accounts):
    work = submission_work
    path = work["path"] + "/submissions/my"
    assert (await api_client.get(path, headers=work["student"])).status_code == 404
    assert (await api_client.get(path)).status_code == 401
    for role in [UserRole.teacher, UserRole.admin]:
        assert (await api_client.get(path, headers=await auth_headers(role))).status_code == 403
    # A submission by another student is never returned, even for a known assignment ID.
    await Submission(assignment_id=work["id"], student_id=PydanticObjectId(), answers=[]).insert()
    assert (await api_client.get(path, headers=work["student"])).status_code == 404
    created = await api_client.post(work["path"] + "/submissions", headers=work["student"], json=work["payload"])
    assert created.status_code == 201
    await api_client.post(work["path"] + "/archive", headers=work["teacher"])
    await work["group"].set({"is_active": False})
    await accounts[UserRole.student].set({"class_id": None})
    pending = await api_client.get(path, headers=work["student"])
    assert pending.status_code == 200 and pending.json()["final_total_score"] is None
    response = await api_client.post(f"/api/v1/submissions/{created.json()['id']}/confirm-grade", headers=work["teacher"], json={
        "grades": [{"question_id": str(work["question"].id), "final_score": 8, "final_comment": "Reviewed"}],
    })
    assert response.status_code == 200
    found = await api_client.get(path, headers=work["student"])
    assert found.json()["final_total_score"] == 8
    assert found.json()["answers"][0]["final_comment"] == "Reviewed"


async def test_unique_index_checks_pair_not_individual_ids(test_app):
    collection = Submission.get_pymongo_collection()
    index = (await collection.index_information())["unique_assignment_student"]
    assert index["unique"] and index["key"] == [("assignment_id", 1), ("student_id", 1)]
    assignment, student = PydanticObjectId(), PydanticObjectId()
    await Submission(assignment_id=assignment, student_id=student, answers=[]).insert()
    with pytest.raises(DuplicateKeyError):
        await Submission(assignment_id=assignment, student_id=student, answers=[]).insert()
    await Submission(assignment_id=assignment, student_id=PydanticObjectId(), answers=[]).insert()
    await Submission(assignment_id=PydanticObjectId(), student_id=student, answers=[]).insert()
    assert await Submission.count() == 3


@pytest.mark.parametrize("invalid", [False, True])
async def test_index_preflight_refuses_bad_legacy_data_without_mutation(test_app, invalid):
    collection = Submission.get_pymongo_collection()
    # Only this fixture-owned random database is changed to simulate an old deployment.
    await collection.drop_index("unique_assignment_student")
    pair = {"assignment_id": PydanticObjectId(), "student_id": PydanticObjectId()}
    records = [{**pair, "student_id": "invalid"}] if invalid else [pair.copy(), pair.copy()]
    await collection.insert_many(records)
    before = await collection.find({}).to_list()
    with pytest.raises(RuntimeError, match="Submission index blocked"):
        await check_submission_index_readiness(collection.database)
    assert await collection.find({}).to_list() == before
    assert "unique_assignment_student" not in await collection.index_information()
