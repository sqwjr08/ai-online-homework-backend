import asyncio
import socket
from datetime import UTC, datetime, timedelta
from io import BytesIO

import httpx
import pytest
import pytest_asyncio
import uvicorn
from PIL import Image

from app.models import User, UserRole

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def live_client(test_app, test_settings):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    test_settings.public_base_url = origin
    # test_app already owns the database lifespan and fixture cleanup; do not initialize twice.
    server = uvicorn.Server(uvicorn.Config(test_app, lifespan="off", access_log=False, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(300):
            if server.started:
                break
            if task.done():
                await task
                pytest.fail("Acceptance HTTP server exited before startup")
            await asyncio.sleep(0.01)
        assert server.started, "Acceptance HTTP server did not start"
        async with httpx.AsyncClient(base_url=origin, timeout=15, trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), 10)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            listener.close()


async def request(client, method, path, code=200, headers=None, **kwargs):
    response = await client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == code, f"{method} {path}: {response.status_code} {response.text}"
    return response.json()


async def login(client, username, password):
    data = await request(client, "POST", "/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {data['access_token']}"}


async def test_live_complete_homework_workflow(live_client, account_password, account_password_hash):
    client = live_client
    # The sole bootstrap write: administrators cannot register through public APIs.
    await User(username="accept_admin", password_hash=account_password_hash, role=UserRole.admin).insert()
    admin = await login(client, "accept_admin", account_password)
    for username in ["accept_teacher", "other_teacher"]:
        await request(client, "POST", "/api/v1/users", 201, admin, json={
            "username": username, "password": account_password, "role": "teacher",
        })
    teacher = await login(client, "accept_teacher", account_password)
    other_teacher = await login(client, "other_teacher", account_password)
    student_a = await request(client, "POST", "/api/v1/auth/register", 201, json={
        "username": "student_a", "password": account_password,
    })
    await request(client, "POST", "/api/v1/users", 201, admin, json={
        "username": "student_b", "password": account_password, "role": "student",
    })
    a = await login(client, "student_a", account_password)
    b = await login(client, "student_b", account_password)
    group_a = await request(client, "POST", "/api/v1/classes", 201, teacher, json={"name": "Example A"})
    group_b = await request(client, "POST", "/api/v1/classes", 201, teacher, json={"name": "Example B"})
    await request(client, "POST", "/api/v1/classes/join", headers=a, json={"code": group_a["code"]})
    await request(client, "POST", "/api/v1/classes/join", headers=b, json={"code": group_b["code"]})
    await request(client, "POST", "/api/v1/classes/join", 409, a, json={"code": group_b["code"]})
    me = await request(client, "GET", "/api/v1/auth/me", headers=a)
    assert me["class_id"] == group_a["id"]
    members = await request(client, "GET", f"/api/v1/classes/{group_a['id']}/members", headers=teacher)
    assert members["total"] == 1 and members["items"][0]["id"] == student_a["id"]

    image = BytesIO()
    with Image.new("RGB", (3, 2), "blue") as source:
        source.save(image, format="PNG")
    upload = await request(client, "POST", "/api/v1/uploads/images", headers=teacher,
                           files={"file": ("example.png", image.getvalue(), "image/png")})
    assert (await client.get(upload["url"])).status_code == 200
    questions = []
    for prompt, answer, score in [("Explain an index.", "SECRET_INDEX, lookup", 10),
                                   ("Explain a transaction.", "SECRET_TRANSACTION, atomicity", 5)]:
        questions.append(await request(client, "POST", "/api/v1/questions", 201, teacher, json={
            "prompt": prompt, "reference_answer": answer, "max_score": score,
            "rubric": "SECRET_RUBRIC: definition and example", "image_urls": [upload["url"]],
        }))
    await request(client, "PATCH", f"/api/v1/questions/{questions[0]['id']}", headers=teacher,
                  json={"prompt": "Explain a database index."})
    search = await request(client, "GET", "/api/v1/questions?q=database", headers=teacher)
    assert search["total"] == 1 and search["items"][0]["id"] == questions[0]["id"]
    await request(client, "GET", "/api/v1/questions", 403, a)
    await request(client, "GET", f"/api/v1/questions/{questions[0]['id']}", 403, other_teacher)
    work = await request(client, "POST", "/api/v1/assignments", 201, teacher, json={
        "title": "Example draft", "class_id": group_a["id"], "status": "draft",
        "questions": [{"question_id": q["id"]} for q in questions],
        "due_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    })
    path = f"/api/v1/assignments/{work['id']}"
    assert await request(client, "GET", "/api/v1/assignments/my", headers=a) == []
    await request(client, "GET", path, 404, a)
    answers = {"answers": [{"question_id": q["id"], "answer_text": "Example explanation"} for q in questions]}
    await request(client, "POST", path + "/submissions", 404, a, json=answers)
    await request(client, "PATCH", path, headers=teacher, json={"title": "Example published work"})
    published = await request(client, "POST", path + "/publish", headers=teacher)
    assert published["question_source"] == "snapshot"
    assert published["questions"][0]["reference_answer"].startswith("SECRET_INDEX")
    await request(client, "PATCH", path, 409, teacher, json={"title": "Cannot overwrite"})
    detail = await request(client, "GET", path, headers=a)
    assert [q["position"] for q in detail["questions"]] == [1, 2]
    assert detail["questions"][0]["image_urls"] == [upload["url"]]
    assert "SECRET_" not in str(detail) and "reference_answer" not in str(detail)
    visible = await request(client, "GET", "/api/v1/assignments/my", headers=a)
    assert [w["id"] for w in visible] == [work["id"]]
    assert await request(client, "GET", "/api/v1/assignments/my", headers=b) == []
    for headers in [b, other_teacher]:
        await request(client, "GET", path, 403, headers)
    await request(client, "POST", path + "/submissions", 403, b, json=answers)

    submitted = await request(client, "POST", path + "/submissions", 201, a, json=answers)
    assert submitted["status"] == "pending_teacher_review" and submitted["final_total_score"] is None
    assert "ai_total_score" not in submitted
    assert all("ai_score" not in item and item["final_score"] is None for item in submitted["answers"])
    await request(client, "POST", path + "/submissions", 409, a, json=answers)
    submission_path = f"/api/v1/submissions/{submitted['id']}"
    await request(client, "GET", submission_path, 403, b)
    await request(client, "GET", path + "/submissions/my", 404, b)
    await request(client, "GET", path + "/submissions", 403, a)
    await request(client, "GET", path + "/submissions", 403, other_teacher)
    page = await request(client, "GET", path + "/submissions?status=pending_teacher_review", headers=teacher)
    assert page["progress"] == {"submitted_count": 1, "pending_count": 1, "confirmed_count": 0}
    assert page["items"][0]["ai_total_score"] is not None
    grades = {"grades": [{"question_id": q["id"], "final_score": score, "final_comment": "Reviewed example"}
                          for q, score in zip(questions, [8.5, 4], strict=True)]}
    await request(client, "POST", submission_path + "/confirm-grade", 403, a, json=grades)
    await request(client, "POST", submission_path + "/confirm-grade", 403, other_teacher, json=grades)
    confirmed = await request(client, "POST", submission_path + "/confirm-grade", headers=teacher, json=grades)
    assert confirmed["status"] == "confirmed" and confirmed["final_total_score"] == 12.5
    for headers in [teacher, admin]:
        await request(client, "POST", submission_path + "/confirm-grade", 409, headers, json=grades)
    for result_path in [submission_path, path + "/submissions/my"]:
        final = await request(client, "GET", result_path, headers=a)
        assert final["final_total_score"] == 12.5 and "ai_total_score" not in final
        assert all(item["final_comment"] == "Reviewed example" and "ai_comment" not in item for item in final["answers"])
    progress = await request(client, "GET", path + "/submissions?status=pending_teacher_review", headers=teacher)
    assert progress["total"] == 0 and progress["progress"]["confirmed_count"] == 1
    await request(client, "POST", path + "/archive", headers=teacher)
    await request(client, "POST", f"/api/v1/classes/{group_a['id']}/archive", headers=teacher)
    await request(client, "GET", path, 404, a)
    historical = await request(client, "GET", path + "/submissions/my", headers=a)
    assert historical["final_total_score"] == 12.5
    assert (await client.get(upload["url"])).status_code == 200


async def test_live_docs_and_error_contract(live_client):
    client = live_client
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/docs")).status_code == 200
    schema = (await client.get("/openapi.json")).json()
    required = {
        "/api/v1/auth/login": "post", "/api/v1/classes": "post", "/api/v1/questions": "post",
        "/api/v1/assignments/{assignment_id}/publish": "post",
        "/api/v1/assignments/{assignment_id}/submissions": "post",
        "/api/v1/submissions/{submission_id}/confirm-grade": "post",
        "/api/v1/assignments/{assignment_id}/submissions/my": "get",
    }
    for path, method in required.items():
        assert method in schema["paths"][path]
    assert "401" in schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert {"400", "401", "403", "404", "409", "422"} <= schema["paths"]["/api/v1/assignments"]["post"]["responses"].keys()
    for path in ["/api/v1/auth/me", "/api/v1/classes/my", "/api/v1/assignments/my"]:
        data = await request(client, "GET", path, 401)
        assert isinstance(data["detail"], str)
    invalid = await request(client, "POST", "/api/v1/auth/login", 422, json={})
    assert isinstance(invalid["detail"], list)
    failed = await request(client, "POST", "/api/v1/auth/login", 401, json={"username": "absent", "password": "not-a-real-password"})
    assert isinstance(failed["detail"], str)
    models = schema["components"]["schemas"]
    assert "ai_total_score" not in models["StudentSubmissionRead"]["properties"]
    assert "reference_answer" not in models["StudentAssignmentQuestionRead"]["properties"]
