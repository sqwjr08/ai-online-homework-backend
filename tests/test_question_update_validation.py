import pytest
from pydantic import ValidationError

from app.schemas import QuestionUpdate


@pytest.mark.parametrize(
    "payload",
    [
        {}, {"prompt": None}, {"reference_answer": None}, {"max_score": None},
        {"image_urls": None}, {"prompt": " \t"}, {"reference_answer": "\u3000"},
        {"rubric": " \n"}, {"rubric": {"points": 10}},
        {"prompt": "x" * 10001}, {"reference_answer": "x" * 20001},
        {"rubric": "x" * 5001}, {"max_score": 0}, {"max_score": -1},
        {"max_score": True}, {"max_score": "10"}, {"max_score": float("inf")},
        {"max_score": float("nan")}, {"created_by": "injected"}, {"is_active": True},
        {"content_locked": False}, {"updated_at": "2026-01-01"}, {"id": "injected"},
    ],
)
def test_invalid_question_update(payload):
    with pytest.raises(ValidationError):
        QuestionUpdate.model_validate(payload)


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"prompt": " New prompt "}, {"prompt": "New prompt"}),
        ({"reference_answer": " A\n B "}, {"reference_answer": "A\n B"}),
        ({"max_score": 2.5}, {"max_score": 2.5}),
        ({"rubric": None}, {"rubric": None}),
        ({"rubric": " Points "}, {"rubric": "Points"}),
        ({"image_urls": []}, {"image_urls": []}),
        ({"image_urls": ["http://testserver/uploads/a.png"]},
         {"image_urls": ["http://testserver/uploads/a.png"]}),
    ],
)
def test_partial_update_only_contains_explicit_fields(payload, expected):
    assert QuestionUpdate.model_validate(payload).model_dump(exclude_unset=True) == expected
