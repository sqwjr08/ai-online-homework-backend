import pytest
from pydantic import ValidationError

from app.schemas import ConfirmGradeRequest, GradeOverride

ID = "012345678901234567890123"


@pytest.mark.parametrize("score", [-1, float("nan"), float("inf"), True, "5", 1.001])
def test_invalid_final_scores(score):
    with pytest.raises(ValidationError):
        GradeOverride(question_id=ID, final_score=score)


def test_grade_request_constraints_and_comment_normalization():
    grade = {"question_id": ID, "final_score": 0}
    for payload in [{"grades": []}, {"grades": [grade], "final_total_score": 100},
                    {"grades": [{**grade, "ai_score": 10}]},
                    {"grades": [{**grade, "final_comment": "a" * 1001}]}]:
        with pytest.raises(ValidationError):
            ConfirmGradeRequest.model_validate(payload)
    assert GradeOverride(**grade, final_comment=" \n ").final_comment is None
    assert GradeOverride(**grade, final_comment=" Reviewed ").final_comment == "Reviewed"
    assert GradeOverride(question_id=ID, final_score=9.99).final_score == 9.99
