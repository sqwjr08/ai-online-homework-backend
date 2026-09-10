import pytest
from pydantic import ValidationError

from app.models import QuestionSnapshot
from app.schemas import AssignmentCreate, StudentAssignmentQuestionRead


@pytest.mark.parametrize("score", [0, -1, float("inf"), float("nan")])
def test_snapshot_score_requires_finite_positive_value(score):
    with pytest.raises(ValidationError):
        QuestionSnapshot(prompt="Q", reference_answer="A", max_score=score)


def test_snapshot_copies_image_list():
    images = ["http://testserver/uploads/example.png"]
    snapshot = QuestionSnapshot(prompt="Q", reference_answer="A", max_score=10, image_urls=images)
    images.append("http://testserver/uploads/changed.png")
    assert snapshot.image_urls == ["http://testserver/uploads/example.png"]


@pytest.mark.parametrize("secret", ["reference_answer", "rubric", "snapshot"])
def test_student_question_schema_rejects_secret_fields(secret):
    with pytest.raises(ValidationError):
        StudentAssignmentQuestionRead(
            question_id="507f1f77bcf86cd799439011", position=1, prompt="Q", max_score=10,
            image_urls=[], **{secret: "SECRET"},
        )


@pytest.mark.parametrize("location", ["assignment", "question"])
def test_assignment_cannot_accept_client_snapshots(location):
    payload = {
        "title": "Work", "class_id": "507f1f77bcf86cd799439011",
        "questions": [{"question_id": "507f1f77bcf86cd799439012"}],
    }
    if location == "assignment":
        payload["snapshot_version"] = 1
    else:
        payload["questions"][0]["snapshot"] = {"reference_answer": "Injected"}
    with pytest.raises(ValidationError):
        AssignmentCreate.model_validate(payload)
