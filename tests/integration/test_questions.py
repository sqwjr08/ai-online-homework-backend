from datetime import UTC, datetime

from beanie import PydanticObjectId
import pytest

from app.models import Question, User, UserRole

pytestmark = pytest.mark.integration

PAYLOAD = {
    "prompt": "  Explain a database index.  ",
    "reference_answer": "  Faster lookup, additional storage, write overhead.\n",
    "max_score": 10,
    "rubric": " Lookup benefit: 4; storage: 3; write cost: 3. ",
    "image_urls": ["http://testserver/uploads/example.png"],
}


@pytest.mark.parametrize("role", [UserRole.teacher, UserRole.admin])
async def test_create_and_read_question(api_client, auth_headers, accounts, role):
    headers = await auth_headers(role)
    response = await api_client.post("/api/v1/questions", headers=headers, json=PAYLOAD)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["prompt"] == PAYLOAD["prompt"].strip()
    assert created["reference_answer"] == PAYLOAD["reference_answer"].strip()
    assert created["rubric"] == PAYLOAD["rubric"].strip()
    assert created["max_score"] == 10
    assert created["image_urls"] == PAYLOAD["image_urls"]
    assert created["created_by"] == str(accounts[role].id)
    assert created["is_active"] is True
    assert created["updated_at"]
    detail = await api_client.get(f"/api/v1/questions/{created['id']}", headers=headers)
    assert detail.status_code == 200
    loaded = detail.json()
    for field in created:
        if field in {"created_at", "updated_at"}:
            # BSON stores UTC timestamps at millisecond precision; the insert response is in memory.
            before = datetime.fromisoformat(created[field]).replace(tzinfo=UTC)
            after = datetime.fromisoformat(loaded[field]).replace(tzinfo=UTC)
            assert 0 <= (before - after).total_seconds() < 0.001
        else:
            assert loaded[field] == created[field]
    stored = await Question.get(PydanticObjectId(created["id"]))
    assert stored.prompt == created["prompt"]
    assert stored.rubric == created["rubric"]


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": " \n\t"}, {"reference_answer": "\u3000"}, {"rubric": " "},
        {"rubric": "x" * 5001}, {"rubric": {"score": 10}}, {"max_score": 0},
        {"max_score": "Infinity"}, {"max_score": True},
        {"created_by": "507f1f77bcf86cd799439011"}, {"is_active": False},
    ],
)
async def test_invalid_question_does_not_write(api_client, auth_headers, change):
    response = await api_client.post(
        "/api/v1/questions", headers=await auth_headers(UserRole.teacher),
        json={**PAYLOAD, **change},
    )
    assert response.status_code == 422
    assert await Question.count() == 0


@pytest.mark.parametrize("authenticated", [False, True])
async def test_question_endpoints_deny_anonymous_and_students(
    api_client, auth_headers, accounts, authenticated,
):
    question = await Question(
        prompt="Private question", reference_answer="Private answer", max_score=10,
        rubric="Private rubric", created_by=accounts[UserRole.teacher].id,
    ).insert()
    headers = await auth_headers(UserRole.student) if authenticated else {}
    expected = 403 if authenticated else 401
    responses = [
        await api_client.post("/api/v1/questions", headers=headers, json=PAYLOAD),
        await api_client.get("/api/v1/questions", headers=headers),
        await api_client.get(f"/api/v1/questions/{question.id}", headers=headers),
    ]
    for response in responses:
        assert response.status_code == expected
        assert "Private answer" not in response.text
        assert "Private rubric" not in response.text
    assert await Question.count() == 1


async def test_detail_ownership_and_inactive_read(api_client, auth_headers, accounts):
    question = await Question(
        prompt="Archived", reference_answer="Answer", max_score=10, is_active=False,
        created_by=accounts[UserRole.teacher].id,
    ).insert()
    other_teacher = await User(
        username="other_teacher", role=UserRole.teacher,
        password_hash=accounts[UserRole.teacher].password_hash,
    ).insert()
    foreign = await Question(
        prompt="Other owner", reference_answer="Hidden answer", max_score=10,
        created_by=other_teacher.id,
    ).insert()
    teacher = await auth_headers(UserRole.teacher)
    response = await api_client.get(f"/api/v1/questions/{question.id}", headers=teacher)
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    response = await api_client.get(f"/api/v1/questions/{foreign.id}", headers=teacher)
    assert response.status_code == 403
    assert "Hidden answer" not in response.text
    response = await api_client.get(
        f"/api/v1/questions/{question.id}", headers=await auth_headers(UserRole.admin),
    )
    assert response.status_code == 200


@pytest.mark.parametrize("question_id,expected", [("not-an-id", 422), ("507f1f77bcf86cd799439011", 404)])
async def test_detail_invalid_and_missing_id(api_client, auth_headers, question_id, expected):
    response = await api_client.get(
        f"/api/v1/questions/{question_id}", headers=await auth_headers(UserRole.teacher),
    )
    assert response.status_code == expected


async def test_search_pagination_ownership_and_status(api_client, auth_headers, accounts):
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    teacher_id = accounts[UserRole.teacher].id
    questions = []
    for prompt, owner, active in [
        ("Explain INDEX [a.*] one", teacher_id, True),
        ("Explain index [a.*] two", teacher_id, True),
        ("Explain index wildcard", teacher_id, True),
        ("Explain index [a.*] inactive", teacher_id, False),
        ("Explain index [a.*] foreign", accounts[UserRole.admin].id, True),
    ]:
        questions.append(await Question(
            prompt=prompt, reference_answer="Answer-only-needle", rubric="Rubric-only-needle",
            max_score=10, created_by=owner, is_active=active, created_at=stamp,
        ).insert())
    teacher = await auth_headers(UserRole.teacher)
    default = (await api_client.get("/api/v1/questions", headers=teacher)).json()
    assert default["total"] == 3
    assert default["page"] == 1 and default["page_size"] == 20
    expected_ids = sorted([str(q.id) for q in questions[:2]], reverse=True)
    observed = []
    for page in [1, 2, 3]:
        response = await api_client.get(
            "/api/v1/questions", headers=teacher,
            params={"q": "  InDeX [a.*]  ", "page": page, "page_size": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2 and data["page"] == page and data["page_size"] == 1
        observed.extend(item["id"] for item in data["items"])
    assert observed == expected_ids
    inactive = await api_client.get(
        "/api/v1/questions", headers=teacher, params={"is_active": "false"},
    )
    assert [q["id"] for q in inactive.json()["items"]] == [str(questions[3].id)]
    admin = await api_client.get(
        "/api/v1/questions", headers=await auth_headers(UserRole.admin),
    )
    assert admin.json()["total"] == 4
    for term in ["Answer-only-needle", "Rubric-only-needle", "no-match"]:
        result = await api_client.get("/api/v1/questions", headers=teacher, params={"q": term})
        assert result.json()["total"] == 0 and result.json()["items"] == []
    empty_query = await api_client.get("/api/v1/questions", headers=teacher, params={"q": "  "})
    assert empty_query.json()["total"] == 3


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": 1000001}, {"page_size": 0}, {"page_size": 101},
     {"q": "x" * 201}, {"is_active": "unknown"}],
)
async def test_invalid_question_list_query(api_client, auth_headers, params):
    response = await api_client.get(
        "/api/v1/questions", headers=await auth_headers(UserRole.teacher), params=params,
    )
    assert response.status_code == 422


async def test_question_openapi_contract(api_client):
    schema = (await api_client.get("/openapi.json")).json()
    paths = schema["paths"]
    listing = paths["/api/v1/questions"]["get"]
    assert listing["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/QuestionPage"
    )
    assert {"page", "page_size", "q", "is_active"} <= {p["name"] for p in listing["parameters"]}
    for path, method in [("/api/v1/questions", "post"), ("/api/v1/questions", "get"),
                         ("/api/v1/questions/{question_id}", "get")]:
        assert {"401", "403", "422"} <= paths[path][method]["responses"].keys()
    assert "404" in paths["/api/v1/questions/{question_id}"]["get"]["responses"]
    models = schema["components"]["schemas"]
    assert models["QuestionCreate"]["additionalProperties"] is False
    assert "is_active" in models["QuestionRead"]["properties"]
    assert "updated_at" in models["QuestionRead"]["properties"]
