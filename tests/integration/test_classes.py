import asyncio

import pytest
from beanie import PydanticObjectId

from app.models import Assignment, ClassGroup, Question, User, UserRole

pytestmark = pytest.mark.integration


@pytest.fixture
async def groups(accounts):
    return [
        await ClassGroup(name=name, code=code, teacher_id=accounts[UserRole.teacher].id).insert()
        for name, code in [("Class A", "AAAAAA"), ("Class B", "BBBBBB")]
    ]


@pytest.mark.parametrize("actor", [UserRole.teacher, UserRole.admin])
async def test_create_class(api_client, accounts, auth_headers, actor):
    response = await api_client.post(
        "/api/v1/classes",
        headers=await auth_headers(actor),
        json={
            "name": " Class A ",
            "teacher_id": str(accounts[UserRole.teacher].id),
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Class A" and len(body["code"]) == 6
    assert "student_ids" not in body
    raw = await ClassGroup.get_pymongo_collection().find_one({"_id": PydanticObjectId(body["id"])})
    assert "student_ids" not in raw


@pytest.mark.parametrize(
    "body", [{"name": "   "}, {"name": "x" * 101}, {"name": "Class", "student_ids": []}]
)
async def test_invalid_class_creation(api_client, auth_headers, body):
    response = await api_client.post(
        "/api/v1/classes", headers=await auth_headers(UserRole.teacher), json=body
    )
    assert response.status_code == 422


async def test_active_teacher_required(api_client, accounts, auth_headers):
    admin = await auth_headers(UserRole.admin)
    teacher = accounts[UserRole.teacher]
    await teacher.set({"is_active": False})
    for teacher_id in [teacher.id, accounts[UserRole.student].id, PydanticObjectId()]:
        response = await api_client.post(
            "/api/v1/classes", headers=admin, json={"name": "Class", "teacher_id": str(teacher_id)}
        )
        assert response.status_code == 400


async def test_class_code_collision_retried(api_client, auth_headers, groups, monkeypatch):
    from app.services import classes

    choices = iter("AAAAAACCCCCC")
    monkeypatch.setattr(classes.secrets, "choice", lambda _: next(choices))
    response = await api_client.post(
        "/api/v1/classes", headers=await auth_headers(UserRole.teacher), json={"name": "Class C"}
    )
    assert response.status_code == 201 and response.json()["code"] == "CCCCCC"


async def test_join_is_idempotent_and_cannot_transfer(api_client, accounts, auth_headers, groups):
    headers = await auth_headers(UserRole.student)
    before = (await User.get(accounts[UserRole.student].id)).model_dump()
    for attempt in range(2):
        response = await api_client.post(
            "/api/v1/classes/join", headers=headers, json={"code": " aaaaaa "}
        )
        assert response.status_code == 200 and "student_ids" not in response.json()
        saved = await User.get(accounts[UserRole.student].id)
        assert saved.class_id == groups[0].id
        assert saved.password_hash == before["password_hash"] and saved.token_version == 0
        if attempt == 0:
            timestamp = saved.updated_at
        else:
            assert saved.updated_at == timestamp
    response = await api_client.post(
        "/api/v1/classes/join", headers=headers, json={"code": "BBBBBB"}
    )
    assert response.status_code == 409
    assert (await User.get(accounts[UserRole.student].id)).class_id == groups[0].id


@pytest.mark.parametrize("same_class", [True, False])
async def test_concurrent_join_has_one_membership(
    api_client, accounts, auth_headers, groups, monkeypatch, same_class
):
    headers = await auth_headers(UserRole.student)
    collection_type = type(User.get_pymongo_collection())
    original = collection_type.update_one
    ready = asyncio.Event()
    arrived = 0

    async def barrier(self, *args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", barrier)
    codes = ["AAAAAA", "AAAAAA" if same_class else "BBBBBB"]
    results = await asyncio.gather(
        *[
            api_client.post("/api/v1/classes/join", headers=headers, json={"code": code})
            for code in codes
        ]
    )
    assert sorted(result.status_code for result in results) == (
        [200, 200] if same_class else [200, 409]
    )
    user = await User.get(accounts[UserRole.student].id)
    assert user.class_id in [group.id for group in groups]
    assert await User.find({"class_id": {"$in": [group.id for group in groups]}}).count() == 1


@pytest.mark.parametrize("code,expected", [("!!!!!!", 422), ("ABC", 422), ("ZZZZZZ", 404)])
async def test_invalid_join_code(api_client, auth_headers, code, expected):
    response = await api_client.post(
        "/api/v1/classes/join", headers=await auth_headers(UserRole.student), json={"code": code}
    )
    assert response.status_code == expected


@pytest.mark.parametrize("role", [UserRole.admin, UserRole.teacher])
async def test_non_student_cannot_join(api_client, auth_headers, groups, role):
    response = await api_client.post(
        "/api/v1/classes/join", headers=await auth_headers(role), json={"code": "AAAAAA"}
    )
    assert response.status_code == 403


async def test_members_come_from_single_reference(
    api_client, accounts, auth_headers, groups, account_password_hash
):
    first = accounts[UserRole.student]
    await first.set({"class_id": groups[0].id})
    second = await User(
        username="second_student",
        password_hash=account_password_hash,
        role=UserRole.student,
        class_id=groups[0].id,
        is_active=False,
    ).insert()
    await ClassGroup.get_pymongo_collection().update_one(
        {"_id": groups[0].id}, {"$set": {"student_ids": [PydanticObjectId()]}}
    )
    headers = await auth_headers(UserRole.teacher)
    path = f"/api/v1/classes/{groups[0].id}/members"
    response = await api_client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert {user["id"] for user in response.json()["items"]} == {str(first.id), str(second.id)}
    assert all(
        "password_hash" not in user and "token_version" not in user
        for user in response.json()["items"]
    )
    response = await api_client.get(path, headers=headers, params={"is_active": False})
    assert response.json()["total"] == 1 and response.json()["items"][0]["id"] == str(second.id)
    pages = []
    for page in [1, 2, 3]:
        response = await api_client.get(
            path, headers=headers, params={"page_size": 1, "page": page}
        )
        assert response.json()["total"] == 2
        pages.extend(user["id"] for user in response.json()["items"])
    assert len(pages) == len(set(pages)) == 2


@pytest.mark.parametrize("endpoint,method", [("members", "GET"), ("archive", "POST")])
@pytest.mark.parametrize("actor", ["student", "other_teacher", "anonymous"])
async def test_class_management_permissions(
    api_client, accounts, auth_headers, groups, account_password_hash, endpoint, method, actor
):
    if actor == "other_teacher":
        other = await User(
            username="other_teacher", password_hash=account_password_hash, role=UserRole.teacher
        ).insert()
        from app.core.security import create_access_token

        headers = {"Authorization": "Bearer " + create_access_token(str(other.id))}
    else:
        headers = await auth_headers(UserRole.student) if actor == "student" else {}
    response = await api_client.request(
        method, f"/api/v1/classes/{groups[0].id}/{endpoint}", headers=headers
    )
    assert response.status_code == (401 if actor == "anonymous" else 403)
    assert (await ClassGroup.get(groups[0].id)).is_active


async def test_my_classes_and_assignment_access_ignore_old_list(
    api_client, accounts, auth_headers, groups
):
    user = accounts[UserRole.student]
    await user.set({"class_id": groups[0].id})
    await ClassGroup.get_pymongo_collection().update_one(
        {"_id": groups[1].id}, {"$set": {"student_ids": [user.id]}}
    )
    assignments = [
        await Assignment(
            title="Work", class_id=g.id, questions=[], created_by=accounts[UserRole.teacher].id
        ).insert()
        for g in groups
    ]
    headers = await auth_headers(UserRole.student)
    response = await api_client.get("/api/v1/classes/my", headers=headers)
    assert [item["id"] for item in response.json()] == [str(groups[0].id)]
    response = await api_client.get("/api/v1/assignments/my", headers=headers)
    assert [item["id"] for item in response.json()] == [str(assignments[0].id)]
    for assignment, expected in zip(assignments, [200, 403]):
        response = await api_client.get(f"/api/v1/assignments/{assignment.id}", headers=headers)
        assert response.status_code == expected


@pytest.mark.parametrize("actor", [UserRole.admin, UserRole.teacher])
async def test_archive_retains_history_but_blocks_new_work(
    api_client, accounts, auth_headers, groups, actor
):
    user = accounts[UserRole.student]
    await user.set({"class_id": groups[0].id})
    question = await Question(
        prompt="Q", reference_answer="A", max_score=10, created_by=accounts[UserRole.teacher].id
    ).insert()
    assignment = await Assignment(
        title="Work",
        class_id=groups[0].id,
        questions=[{"question_id": question.id}],
        created_by=accounts[UserRole.teacher].id,
    ).insert()
    student = await auth_headers(UserRole.student)
    submitted = await api_client.post(
        f"/api/v1/assignments/{assignment.id}/submissions",
        headers=student,
        json={"answers": [{"question_id": str(question.id), "answer_text": "A"}]},
    )
    assert submitted.status_code == 201
    submission_id = submitted.json()["id"]
    headers = await auth_headers(actor)
    path = f"/api/v1/classes/{groups[0].id}/archive"
    assert (await api_client.post(path, headers=headers)).status_code == 200
    timestamp = (await ClassGroup.get(groups[0].id)).updated_at
    assert (await api_client.post(path, headers=headers)).status_code == 200
    assert (await ClassGroup.get(groups[0].id)).updated_at == timestamp
    assert (await User.get(user.id)).class_id == groups[0].id
    student = await auth_headers(UserRole.student)
    assert (
        await api_client.get(f"/api/v1/assignments/{assignment.id}", headers=student)
    ).status_code == 200
    response = await api_client.post(
        "/api/v1/classes/join", headers=student, json={"code": "AAAAAA"}
    )
    assert response.status_code == 404
    response = await api_client.post(
        f"/api/v1/assignments/{assignment.id}/submissions",
        headers=student,
        json={"answers": [{"question_id": str(question.id), "answer_text": "A"}]},
    )
    assert response.status_code == 409
    response = await api_client.post(
        "/api/v1/assignments",
        headers=headers,
        json={
            "title": "New",
            "class_id": str(groups[0].id),
            "questions": [{"question_id": str(question.id)}],
        },
    )
    assert response.status_code == 404
    response = await api_client.get(
        "/api/v1/classes/my", headers=student, params={"is_active": False}
    )
    assert len(response.json()) == 1 and not response.json()[0]["is_active"]
    assert (
        await api_client.get(f"/api/v1/classes/{groups[0].id}/members", headers=headers)
    ).status_code == 200
    confirmed = await api_client.post(
        f"/api/v1/submissions/{submission_id}/confirm-grade",
        headers=headers,
        json={
            "grades": [
                {"question_id": str(question.id), "final_score": 9, "final_comment": "Reviewed"}
            ]
        },
    )
    assert confirmed.status_code == 200
    history = await api_client.get(f"/api/v1/submissions/{submission_id}", headers=student)
    assert history.status_code == 200 and history.json()["final_total_score"] == 9


async def test_class_query_index_and_openapi(api_client):
    indexes = await User.get_pymongo_collection().index_information()
    assert indexes["class_id_1"]["key"] == [("class_id", 1)]
    assert not indexes["class_id_1"].get("unique", False)
    schema = (await api_client.get("/openapi.json")).json()
    assert "student_ids" not in schema["components"]["schemas"]["ClassRead"]["properties"]
    assert "/api/v1/classes/{class_id}/members" in schema["paths"]
    assert "/api/v1/classes/{class_id}/archive" in schema["paths"]


@pytest.mark.parametrize("suffix,method", [("members", "GET"), ("archive", "POST")])
@pytest.mark.parametrize("class_id,code", [("invalid", 422), (str(PydanticObjectId()), 404)])
async def test_invalid_class_targets(api_client, auth_headers, suffix, method, class_id, code):
    response = await api_client.request(
        method, f"/api/v1/classes/{class_id}/{suffix}", headers=await auth_headers(UserRole.admin)
    )
    assert response.status_code == code


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 101}, {"is_active": "wrong"}])
async def test_member_query_validation(api_client, auth_headers, groups, params):
    response = await api_client.get(
        f"/api/v1/classes/{groups[0].id}/members",
        headers=await auth_headers(UserRole.teacher),
        params=params,
    )
    assert response.status_code == 422


async def test_different_students_can_join_same_class_concurrently(
    api_client,
    accounts,
    auth_headers,
    groups,
    account_password_hash,
):
    from app.core.security import create_access_token

    second = await User(
        username="second_student", role=UserRole.student, password_hash=account_password_hash
    ).insert()
    headers = [
        await auth_headers(UserRole.student),
        {"Authorization": "Bearer " + create_access_token(str(second.id))},
    ]
    results = await asyncio.gather(
        *[
            api_client.post("/api/v1/classes/join", headers=header, json={"code": "AAAAAA"})
            for header in headers
        ]
    )
    assert all(response.status_code == 200 for response in results)
    assert await User.find(User.class_id == groups[0].id).count() == 2


async def test_legacy_missing_class_id_can_join(api_client, accounts, auth_headers, groups):
    student = accounts[UserRole.student]
    await User.get_pymongo_collection().update_one(
        {"_id": student.id}, {"$unset": {"class_id": "", "token_version": ""}}
    )
    response = await api_client.post(
        "/api/v1/classes/join",
        headers=await auth_headers(UserRole.student),
        json={"code": "AAAAAA"},
    )
    assert response.status_code == 200
    assert (await User.get(student.id)).class_id == groups[0].id
