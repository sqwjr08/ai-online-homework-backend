from dataclasses import dataclass

from app.models import QuestionSnapshot


@dataclass
class GradeResult:
    score: float
    comment: str


class GradingService:
    async def grade_short_answer(self, question: QuestionSnapshot, answer_text: str) -> GradeResult:
        raise NotImplementedError


class PlaceholderGradingService(GradingService):
    async def grade_short_answer(self, question: QuestionSnapshot, answer_text: str) -> GradeResult:
        normalized_answer = answer_text.strip()
        if not normalized_answer:
            return GradeResult(score=0, comment="未作答。")

        reference_terms = {
            term
            for term in question.reference_answer.replace("，", ",").replace("。", ",").split(",")
            if term.strip()
        }
        matched_terms = [term for term in reference_terms if term.strip() in normalized_answer]
        if reference_terms:
            score_ratio = len(matched_terms) / len(reference_terms)
        else:
            score_ratio = 0.6
        score = round(max(0.0, min(question.max_score, question.max_score * score_ratio)), 2)
        comment = "AI 草稿：答案已提交，请教师复核。"
        if matched_terms:
            comment = f"AI 草稿：覆盖了 {len(matched_terms)} 个参考要点，请教师复核。"
        return GradeResult(score=score, comment=comment)


def get_grading_service() -> GradingService:
    return PlaceholderGradingService()
