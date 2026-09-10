from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import get_current_user, require_roles
from app.models import Assignment, AssignmentQuestion, AssignmentStatus, ClassGroup, User, UserRole
from app.schemas import AssignmentCreate, AssignmentUpdate, AssignmentRead, AssignmentResponse, StudentAssignmentRead
from app.services.assignments import (
    assignment_read, referenced_content, ensure_deadline_open,
    update_assignment_atomically, validate_assignment_questions,
)
from app.services.questions import lock_assignment_questions

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
        if assignment.status != AssignmentStatus.published:
            raise HTTPException(status_code=404, detail="Assignment not found")
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post(
    "", response_model=AssignmentRead, status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Invalid questions or deadline has passed"},
               401: {"description": "Not authenticated"},
               403: {"description": "Teacher role or ownership required"},
               404: {"description": "Active class not found"},
               409: {"description": "Question changed or referenced content requires repair"}},
)
async def create_assignment(
    payload: AssignmentCreate,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> AssignmentRead:
    class_group = await ClassGroup.get(payload.class_id)
    if not class_group or not class_group.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    if current_user.role == UserRole.teacher and class_group.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Teacher does not own class"
        )

    question_ids = [item.question_id for item in payload.questions]
    await validate_assignment_questions(question_ids, current_user)

    assignment = Assignment(
        title=payload.title,
        description=payload.description,
        class_id=payload.class_id,
        questions=[AssignmentQuestion(question_id=item.question_id) for item in payload.questions],
        due_at=payload.due_at,
        status=payload.status,
        created_by=current_user.id,
    )
    if assignment.status == AssignmentStatus.published:
        ensure_deadline_open(assignment)
    await lock_assignment_questions(question_ids, current_user)
    if assignment.status == AssignmentStatus.published:
        # Read after the lock: a successful edit immediately before locking must be captured.
        contents = await referenced_content(assignment)
        assignment.questions = [
            AssignmentQuestion(question_id=item.question_id, snapshot=content)
            for item, content in zip(assignment.questions, contents, strict=True)
        ]
        assignment.snapshot_version = 1
        ensure_deadline_open(assignment)
    await assignment.insert()
    return await assignment_read(assignment, current_user)


@router.get(
    "/my", response_model=list[AssignmentResponse],
    responses={401: {"description": "Not authenticated"},
               409: {"description": "Assignment snapshots or legacy references require repair"}},
)
async def my_assignments(
    status: AssignmentStatus | None = None,
    current_user: User = Depends(get_current_user),
) -> list[AssignmentRead | StudentAssignmentRead]:
    filters = {"status": status} if status is not None else {}
    if current_user.role == UserRole.admin:
        assignments = await Assignment.find(filters).sort("-created_at", "-_id").to_list()
    else:
        if current_user.role == UserRole.teacher:
            class_groups = await ClassGroup.find(ClassGroup.teacher_id == current_user.id).to_list()
            accessible_class_ids = [item.id for item in class_groups]
        else:
            accessible_class_ids = [current_user.class_id] if current_user.class_id else []
        if not accessible_class_ids:
            return []
        filters["class_id"] = {"$in": accessible_class_ids}
        if current_user.role == UserRole.student:
            if status is not None and status != AssignmentStatus.published:
                return []
            filters["status"] = AssignmentStatus.published
        assignments = await Assignment.find(filters).sort("-created_at", "-_id").to_list()
    return [await assignment_read(item, current_user) for item in assignments]


@router.get(
    "/{assignment_id}", response_model=AssignmentResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Assignment access denied"},
        404: {"description": "Assignment not found or not published for students"},
        409: {"description": "Assignment snapshots or legacy references require repair"},
    },
)
async def get_assignment(
    assignment_id: PydanticObjectId,
    current_user: User = Depends(get_current_user),
) -> AssignmentRead | StudentAssignmentRead:
    assignment = await Assignment.get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await ensure_assignment_access(assignment, current_user)
    return await assignment_read(assignment, current_user)


async def managed_assignment(assignment_id: PydanticObjectId, actor: User, *, active: bool) -> Assignment:
    assignment = await Assignment.get(assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await ensure_assignment_access(assignment, actor)
    if active:
        group = await ClassGroup.get(assignment.class_id)
        if not group or not group.is_active:
            raise HTTPException(status_code=409, detail="Class is archived")
    return assignment


mutation_errors = {
    400: {"description": "Invalid questions or deadline has passed"},
    401: {"description": "Not authenticated"},
    403: {"description": "Assignment access denied"},
    404: {"description": "Assignment or class not found"},
    409: {"description": "Invalid state, archived class, changed assignment or damaged content"},
}


@router.patch("/{assignment_id}", response_model=AssignmentRead, responses=mutation_errors)
async def edit_assignment(
    assignment_id: PydanticObjectId,
    payload: AssignmentUpdate,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> AssignmentRead:
    assignment = await managed_assignment(assignment_id, current_user, active=True)
    if assignment.status != AssignmentStatus.draft:
        raise HTTPException(status_code=409, detail="Only drafts can be edited")
    changes = payload.model_dump(exclude_unset=True)
    if payload.questions is not None:
        ids = [item.question_id for item in payload.questions]
        await validate_assignment_questions(ids, current_user)
        await lock_assignment_questions(ids, current_user)
        changes["questions"] = [AssignmentQuestion(question_id=item).model_dump() for item in ids]
    # Validate the prospective draft before writing, including unchanged legacy references.
    await assignment_read(assignment.model_copy(update={
        **changes,
        "questions": [AssignmentQuestion.model_validate(item) for item in changes["questions"]]
        if "questions" in changes else assignment.questions,
    }), current_user)
    updated = await update_assignment_atomically(assignment, changes)
    return await assignment_read(updated, current_user)


@router.post("/{assignment_id}/publish", response_model=AssignmentRead, responses=mutation_errors)
async def publish_assignment(
    assignment_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> AssignmentRead:
    assignment = await managed_assignment(assignment_id, current_user, active=True)
    if assignment.status == AssignmentStatus.published:
        return await assignment_read(assignment, current_user)
    if assignment.status != AssignmentStatus.draft:
        raise HTTPException(status_code=409, detail="Only drafts can be published")
    # Reject malformed stored snapshots rather than silently replacing them.
    await assignment_read(assignment, current_user)
    ensure_deadline_open(assignment)
    ids = [item.question_id for item in assignment.questions]
    await validate_assignment_questions(ids, current_user)
    await lock_assignment_questions(ids, current_user)
    contents = await referenced_content(assignment)
    ensure_deadline_open(assignment)
    updated = await update_assignment_atomically(assignment, {
        "status": AssignmentStatus.published,
        "snapshot_version": 1,
        "questions": [AssignmentQuestion(question_id=item, snapshot=content).model_dump()
                      for item, content in zip(ids, contents, strict=True)],
    })
    return await assignment_read(updated, current_user)


@router.post("/{assignment_id}/archive", response_model=AssignmentRead, responses=mutation_errors)
async def archive_assignment(
    assignment_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> AssignmentRead:
    assignment = await managed_assignment(assignment_id, current_user, active=False)
    if assignment.status == AssignmentStatus.archived:
        return await assignment_read(assignment, current_user)
    await assignment_read(assignment, current_user)
    updated = await update_assignment_atomically(assignment, {
        "status": AssignmentStatus.archived, "archived_from": assignment.status,
    })
    return await assignment_read(updated, current_user)
