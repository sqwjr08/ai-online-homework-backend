from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import get_current_user, require_roles
from app.models import Assignment, AssignmentQuestion, ClassGroup, Question, User, UserRole
from app.schemas import AssignmentCreate, AssignmentRead

router = APIRouter(prefix="/assignments", tags=["assignments"])


async def ensure_assignment_access(assignment: Assignment, user: User) -> None:
    class_group = await ClassGroup.get(assignment.class_id)
    if not class_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    if user.role == UserRole.admin:
        return
    if user.role == UserRole.teacher and class_group.teacher_id == user.id:
        return
    if user.role == UserRole.student and user.class_id == class_group.id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post("", response_model=AssignmentRead, status_code=status.HTTP_201_CREATED)
async def create_assignment(
    payload: AssignmentCreate,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Assignment:
    if not payload.questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="At least one question is required"
        )

    class_group = await ClassGroup.get(payload.class_id)
    if not class_group or not class_group.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    if current_user.role == UserRole.teacher and class_group.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Teacher does not own class"
        )

    question_ids = [item.question_id for item in payload.questions]
    questions = await Question.find({"_id": {"$in": question_ids}, "is_active": True}).to_list()
    if len(questions) != len(set(question_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question not found")
    if current_user.role == UserRole.teacher:
        foreign_question = next(
            (item for item in questions if item.created_by != current_user.id), None
        )
        if foreign_question:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot use another teacher's question",
            )

    assignment = Assignment(
        title=payload.title,
        description=payload.description,
        class_id=payload.class_id,
        questions=[AssignmentQuestion(question_id=item.question_id) for item in payload.questions],
        due_at=payload.due_at,
        status=payload.status,
        created_by=current_user.id,
    )
    await assignment.insert()
    return assignment


@router.get("/my", response_model=list[AssignmentRead])
async def my_assignments(current_user: User = Depends(get_current_user)) -> list[Assignment]:
    if current_user.role == UserRole.admin:
        return await Assignment.find_all().sort("-created_at").to_list()

    if current_user.role == UserRole.teacher:
        class_groups = await ClassGroup.find(ClassGroup.teacher_id == current_user.id).to_list()
        accessible_class_ids = [item.id for item in class_groups]
    else:
        accessible_class_ids = [current_user.class_id] if current_user.class_id else []

    if not accessible_class_ids:
        return []
    return (
        await Assignment.find({"class_id": {"$in": accessible_class_ids}})
        .sort("-created_at")
        .to_list()
    )


@router.get("/{assignment_id}", response_model=AssignmentRead)
async def get_assignment(
    assignment_id: PydanticObjectId,
    current_user: User = Depends(get_current_user),
) -> Assignment:
    assignment = await Assignment.get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await ensure_assignment_access(assignment, current_user)
    return assignment
