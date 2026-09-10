import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas import QuestionCreate


def question_payload(**changes):
    return {
        "prompt": "Explain a database index.",
        "reference_answer": "Speeds up lookup, costs storage and writes.",
        "max_score": 10,
        "rubric": "Lookup benefit: 5 points; tradeoffs: 5 points.",
        **changes,
    }


@pytest.mark.parametrize("field", ["prompt", "reference_answer", "rubric"])
@pytest.mark.parametrize("blank", ["", " \t\n ", "\u3000\u00a0"])
def test_question_text_rejects_blank(field, blank):
    with pytest.raises(ValidationError):
        QuestionCreate(**question_payload(**{field: blank}))


@pytest.mark.parametrize(
    "field,limit", [("prompt", 10_000), ("reference_answer", 20_000), ("rubric", 5_000)]
)
def test_question_text_limits_apply_after_trim(field, limit):
    payload = QuestionCreate(**question_payload(**{field: " " + "x" * limit + "\n"}))
    assert getattr(payload, field) == "x" * limit
    with pytest.raises(ValidationError):
        QuestionCreate(**question_payload(**{field: "x" * (limit + 1)}))


@pytest.mark.parametrize("field", ["prompt", "reference_answer", "rubric"])
def test_question_text_preserves_internal_formatting(field):
    result = QuestionCreate(**question_payload(**{field: "  First line\n  second line.  "}))
    assert getattr(result, field) == "First line\n  second line."


@pytest.mark.parametrize("score", [0, -1, float("nan"), float("inf"), -float("inf"), True, "10", None])
def test_question_score_rejects_invalid_values(score):
    with pytest.raises(ValidationError):
        QuestionCreate(**question_payload(max_score=score))


@pytest.mark.parametrize("score", [1, 2.5, 10001])
def test_question_score_accepts_positive_finite_numbers(score):
    assert QuestionCreate(**question_payload(max_score=score)).max_score == score


@pytest.mark.parametrize("field", ["created_by", "is_active", "id", "updated_at"])
def test_question_create_rejects_server_managed_fields(field):
    with pytest.raises(ValidationError):
        QuestionCreate(**question_payload(**{field: "injected"}))


def test_rubric_remains_optional_plain_text():
    payload = question_payload()
    del payload["rubric"]
    assert QuestionCreate(**payload).rubric is None
    assert QuestionCreate(**question_payload(rubric=None)).rubric is None
    assert QuestionCreate(**question_payload()).rubric is not None
    with pytest.raises(ValidationError):
        QuestionCreate(**question_payload(rubric={"score": 10}))


@pytest.mark.parametrize("field", ["prompt", "reference_answer"])
def test_required_question_text_cannot_be_null(field):
    with pytest.raises(ValidationError):
        QuestionCreate(**question_payload(**{field: None}))


def test_documented_question_examples_validate():
    path = Path(__file__).resolve().parents[1] / "docs" / "examples" / "questions.json"
    examples = json.loads(path.read_text(encoding="utf-8"))
    assert len(examples) == 3
    for example in examples:
        parsed = QuestionCreate.model_validate(example)
        assert parsed.rubric and parsed.max_score > 0
