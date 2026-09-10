from typing import Annotated

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo.errors import DuplicateKeyError

from app.api.routes.assignments import ensure_assignment_access
from app.core.deps import get_current_user, require_roles
from app.models import (
    Assignment,
    ClassGroup,
    Submission,
    SubmissionAnswer,
    SubmissionStatus,
    User,
    UserRole,
)
from app.schemas import (
    ConfirmGradeRequest, SubmissionCreate, SubmissionRead, StudentSubmissionRead,
    SubmissionResponse, SubmissionPage,
)
from app.services.grading import GradingService, get_grading_service
from app.services.assignments import assignment_content, ensure_deadline_open
from app.services.submissions import confirm_submission_grade, submission_page, submission_read

router = APIRouter(tags=["submissions"])


async def ensure_submission_teacher_access(submission: Submission, teacher: User) -> Assignment:
    assignment = await Assignment.get(submission.assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    class_group = await ClassGroup.get(assignment.class_id)
    if not class_group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    if teacher.role == UserRole.admin:
        return assignment
    if teacher.role == UserRole.teacher and class_group.teacher_id == teacher.id:
        return assignment
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.post(
    "/assignments/{assignment_id}/submissions",
    response_model=StudentSubmissionRead,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "Answer mismatch or deadline has passed"},
               401: {"description": "Not authenticated"},
               403: {"description": "Student role or class membership required"},
               404: {"description": "Assignment not found or not visible"},
               409: {"description": "Duplicate submission, archived class or content requires repair"}},
)
async def submit_assignment(
    assignment_id: PydanticObjectId,
    payload: SubmissionCreate,
    current_user: User = Depends(require_roles(UserRole.student)),
    grading_service: GradingService = Depends(get_grading_service),
) -> StudentSubmissionRead:
    assignment = await Assignment.get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await ensure_assignment_access(assignment, current_user)
    class_group = await ClassGroup.get(assignment.class_id)
    if not class_group or not class_group.is_active:
        raise HTTPException(status_code=409, detail="Class is archived")
    ensure_deadline_open(assignment)

    existing = await Submission.find_one(
        Submission.assignment_id == assignment.id,
        Submission.student_id == current_user.id,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Assignment already submitted"
        )

    expected_ids = [item.question_id for item in assignment.questions]
    if not expected_ids or len(expected_ids) != len(set(expected_ids)):
        raise HTTPException(status_code=409, detail="Assignment questions require repair")
    provided_ids = [item.question_id for item in payload.answers]
    if set(expected_ids) != set(provided_ids) or len(provided_ids) != len(set(provided_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Answers must match assignment questions",
        )

    _, contents = await assignment_content(assignment)
    question_by_id = dict(zip(expected_ids, contents, strict=True))
    answers: list[SubmissionAnswer] = []
    ai_total = 0.0
    for item in payload.answers:
        question = question_by_id.get(item.question_id)
        if not question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Question not found"
            )
        grade = await grading_service.grade_short_answer(question, item.answer_text)
        ai_total += grade.score
        answers.append(
            SubmissionAnswer(
                question_id=item.question_id,
                answer_text=item.answer_text,
                ai_score=grade.score,
                ai_comment=grade.comment,
            )
        )

    # Grading may take time; recheck admission before persisting the answer.
    latest = await Assignment.get(assignment.id)
    if latest is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await ensure_assignment_access(latest, current_user)
    class_group = await ClassGroup.get(latest.class_id)
    if not class_group or not class_group.is_active:
        raise HTTPException(status_code=409, detail="Class is archived")
    ensure_deadline_open(latest)
    submission = Submission(
        assignment_id=assignment.id,
        student_id=current_user.id,
        answers=answers,
        ai_total_score=round(ai_total, 2),
    )
    try:
        await submission.insert()
    except DuplicateKeyError:
        if await Submission.find_one({"assignment_id": assignment.id, "student_id": current_user.id}):
            raise HTTPException(status_code=409, detail="Assignment already submitted") from None
        raise
    return submission_read(submission, current_user)


@router.get(
    "/assignments/{assignment_id}/submissions/my", response_model=StudentSubmissionRead,
    responses={401: {"description": "Not authenticated"},
               403: {"description": "Students only"},
               404: {"description": "No submission belonging to the current student"}},
)
async def get_my_submission(
    assignment_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.student)),
) -> StudentSubmissionRead:
    # Ownership, not current class or assignment visibility, controls historical answer access.
    submission = await Submission.find_one({
        "assignment_id": assignment_id, "student_id": current_user.id,
    })
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission_read(submission, current_user)


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse, responses={
    401: {"description": "Not authenticated"},
    403: {"description": "Submission access denied"},
    404: {"description": "Submission, assignment or class not found"},
})
async def get_submission(
    submission_id: PydanticObjectId,
    current_user: User = Depends(get_current_user),
) -> SubmissionRead | StudentSubmissionRead:
    submission = await Submission.get(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    if current_user.role == UserRole.student and submission.student_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if current_user.role in {UserRole.admin, UserRole.teacher}:
        await ensure_submission_teacher_access(submission, current_user)
    return submission_read(submission, current_user)


@router.get(
    "/assignments/{assignment_id}/submissions", response_model=SubmissionPage,
    responses={401: {"description": "Not authenticated"},
               403: {"description": "Class teacher or administrator required"},
               404: {"description": "Assignment or class not found"}},
)
async def list_submissions(
    assignment_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: SubmissionStatus | None = None,
) -> SubmissionPage:
    assignment = await Assignment.get(assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await ensure_assignment_access(assignment, current_user)
    return await submission_page(assignment, page, page_size, status)


@router.post(
    "/submissions/{submission_id}/confirm-grade", response_model=SubmissionRead,
    responses={400: {"description": "Grades mismatch or score exceeds snapshot maximum"},
               401: {"description": "Not authenticated"},
               403: {"description": "Class teacher or administrator required"},
               404: {"description": "Submission, assignment or class not found"},
               409: {"description": "Grade locked, concurrent confirmation or data requires repair"}},
)
async def confirm_grade(
    submission_id: PydanticObjectId,
    payload: ConfirmGradeRequest,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Submission:
    submission = await Submission.get(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    assignment = await ensure_submission_teacher_access(submission, current_user)

    return await confirm_submission_grade(submission, assignment, payload, current_user)
