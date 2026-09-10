import pytest
from pydantic import ValidationError

from app.schemas import SubmissionCreate

QUESTION_ID = "012345678901234567890123"


@pytest.mark.parametrize("text", ["", " \n\t\u3000", "a" * 20001, None])
def test_invalid_answer_text(text):
    with pytest.raises(ValidationError):
        SubmissionCreate(answers=[{"question_id": QUESTION_ID, "answer_text": text}])


def test_preserves_answer_format_and_maximum_length():
    text = " \n" + "a" * 19996 + "\n "
    value = SubmissionCreate(answers=[{"question_id": QUESTION_ID, "answer_text": text}])
    assert value.answers[0].answer_text == text


def test_empty_answers_and_grade_or_owner_injection_rejected():
    answer = {"question_id": QUESTION_ID, "answer_text": "Example"}
    for payload in [
        {"answers": []}, {"answers": [answer], "student_id": QUESTION_ID},
        {"answers": [answer], "status": "confirmed"},
        {"answers": [{**answer, "ai_score": 10}]},
        {"answers": [{**answer, "final_score": 10}]},
    ]:
        with pytest.raises(ValidationError):
            SubmissionCreate.model_validate(payload)
