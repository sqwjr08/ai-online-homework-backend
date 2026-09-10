import re
from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query

from app.core.deps import require_roles
from app.models import Question, User, UserRole
from app.schemas import QuestionCreate, QuestionPage, QuestionRead, QuestionUpdate
from app.services.questions import change_question, teacher_question

router = APIRouter(prefix="/questions", tags=["questions"])

QUESTION_ERRORS = {
    401: {"description": "Not authenticated or token revoked"},
    403: {"description": "Teacher or administrator required; teachers own their questions"},
}


@router.post("", response_model=QuestionRead, status_code=201, responses=QUESTION_ERRORS)
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


@router.get("", response_model=QuestionPage, responses=QUESTION_ERRORS)
async def list_questions(
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200, description="Literal prompt search")] = None,
    is_active: bool = True,
) -> QuestionPage:
    filters = {"is_active": is_active}
    if current_user.role == UserRole.teacher:
        filters["created_by"] = current_user.id
    if q and q.strip():
        filters["prompt"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    total = await Question.find(filters).count()
    questions = (
        await Question.find(filters)
        .sort("-created_at", "-_id")
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list()
    )
    return QuestionPage(
        items=[QuestionRead.model_validate(question, from_attributes=True) for question in questions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{question_id}",
    response_model=QuestionRead,
    responses={**QUESTION_ERRORS, 404: {"description": "Question not found"}},
)
async def get_question(
    question_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Question:
    return await teacher_question(question_id, current_user)


QUESTION_WRITE_ERRORS = {
    **QUESTION_ERRORS,
    404: {"description": "Question not found"},
    409: {"description": "Question disabled, referenced, content locked or concurrently changed"},
}


@router.patch("/{question_id}", response_model=QuestionRead, responses=QUESTION_WRITE_ERRORS)
async def edit_question(
    question_id: PydanticObjectId,
    payload: QuestionUpdate,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Question:
    return await change_question(question_id, current_user, payload)


@router.post("/{question_id}/disable", response_model=QuestionRead, responses=QUESTION_WRITE_ERRORS)
async def disable_question(
    question_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Question:
    return await change_question(question_id, current_user)
