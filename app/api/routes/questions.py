from fastapi import APIRouter, Depends

from app.core.deps import require_roles
from app.models import Question, User, UserRole
from app.schemas import QuestionCreate, QuestionRead

router = APIRouter(prefix="/questions", tags=["questions"])


@router.post("", response_model=QuestionRead, status_code=201)
async def create_question(
    payload: QuestionCreate,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Question:
    question = Question(
        prompt=payload.prompt,
        reference_answer=payload.reference_answer,
        max_score=payload.max_score,
        rubric=payload.rubric,
        image_urls=payload.image_urls,
        created_by=current_user.id,
    )
    await question.insert()
    return question


@router.get("", response_model=list[QuestionRead])
async def list_questions(
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> list[Question]:
    if current_user.role == UserRole.admin:
        return await Question.find(Question.is_active == True).sort("-created_at").to_list()  # noqa: E712
    return (
        await Question.find(Question.is_active == True, Question.created_by == current_user.id)  # noqa: E712
        .sort("-created_at")
        .to_list()
    )
