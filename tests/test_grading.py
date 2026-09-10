import pytest

from app.models import QuestionSnapshot
from app.services.grading import PlaceholderGradingService


@pytest.mark.asyncio
async def test_placeholder_grading_scores_matched_reference_terms():
    question = QuestionSnapshot(
        prompt="简述液压系统的作用。",
        reference_answer="传递动力,控制运动",
        max_score=10,
    )
    service = PlaceholderGradingService()

    result = await service.grade_short_answer(question, "液压系统可以传递动力，并控制运动。")

    assert result.score == 10
    assert "AI 草稿" in result.comment


@pytest.mark.asyncio
async def test_placeholder_grading_returns_zero_for_empty_answer():
    question = QuestionSnapshot(
        prompt="简述液压系统的作用。",
        reference_answer="传递动力,控制运动",
        max_score=10,
    )
    service = PlaceholderGradingService()

    result = await service.grade_short_answer(question, "   ")

    assert result.score == 0
    assert result.comment == "未作答。"
