from datetime import UTC, datetime

from beanie.odm.fields import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routes.assignments import ensure_assignment_access
from app.core.deps import get_current_user, require_roles
from app.models import (
    Assignment,
    ClassGroup,
    Question,
    Submission,
    SubmissionAnswer,
    SubmissionStatus,
    User,
    UserRole,
)
from app.schemas import ConfirmGradeRequest, SubmissionCreate, SubmissionRead
from app.services.grading import GradingService, get_grading_service

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


async def redact_unconfirmed_for_student(submission: Submission, user: User) -> Submission:
    if user.role != UserRole.student or submission.status == SubmissionStatus.confirmed:
        return submission
    redacted = submission.model_copy(deep=True)
    redacted.ai_total_score = None
    redacted.final_total_score = None
    for answer in redacted.answers:
        answer.ai_score = None
        answer.ai_comment = None
        answer.final_score = None
        answer.final_comment = None
    return redacted


@router.post(
    "/assignments/{assignment_id}/submissions",
    response_model=SubmissionRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_assignment(
    assignment_id: PydanticObjectId,
    payload: SubmissionCreate,
    current_user: User = Depends(require_roles(UserRole.student)),
    grading_service: GradingService = Depends(get_grading_service),
) -> Submission:
    assignment = await Assignment.get(assignment_id)
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    await ensure_assignment_access(assignment, current_user)
    class_group = await ClassGroup.get(assignment.class_id)
    if not class_group or not class_group.is_active:
        raise HTTPException(status_code=409, detail="Class is archived")
    if assignment.due_at and assignment.due_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Assignment is closed")

    existing = await Submission.find_one(
        Submission.assignment_id == assignment.id,
        Submission.student_id == current_user.id,
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Assignment already submitted"
        )

    expected_ids = [item.question_id for item in assignment.questions]
    provided_ids = [item.question_id for item in payload.answers]
    if set(expected_ids) != set(provided_ids) or len(provided_ids) != len(set(provided_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Answers must match assignment questions",
        )

    questions = await Question.find({"_id": {"$in": expected_ids}, "is_active": True}).to_list()
    question_by_id = {item.id: item for item in questions}
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

    submission = Submission(
        assignment_id=assignment.id,
        student_id=current_user.id,
        answers=answers,
        ai_total_score=round(ai_total, 2),
    )
    await submission.insert()
    return await redact_unconfirmed_for_student(submission, current_user)


@router.get("/submissions/{submission_id}", response_model=SubmissionRead)
async def get_submission(
    submission_id: PydanticObjectId,
    current_user: User = Depends(get_current_user),
) -> Submission:
    submission = await Submission.get(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    if current_user.role == UserRole.student and submission.student_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if current_user.role in {UserRole.admin, UserRole.teacher}:
        await ensure_submission_teacher_access(submission, current_user)
    return await redact_unconfirmed_for_student(submission, current_user)


@router.post("/submissions/{submission_id}/confirm-grade", response_model=SubmissionRead)
async def confirm_grade(
    submission_id: PydanticObjectId,
    payload: ConfirmGradeRequest,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> Submission:
    submission = await Submission.get(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    assignment = await ensure_submission_teacher_access(submission, current_user)

    assignment_question_ids = [item.question_id for item in assignment.questions]
    questions = await Question.find(
        {"_id": {"$in": assignment_question_ids}, "is_active": True}
    ).to_list()
    max_score_by_id = {item.id: item.max_score for item in questions}
    override_by_id = {item.question_id: item for item in payload.grades}
    if set(override_by_id) != {answer.question_id for answer in submission.answers}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Grades must match submission answers"
        )

    final_total = 0.0
    for answer in submission.answers:
        override = override_by_id[answer.question_id]
        max_score = max_score_by_id.get(answer.question_id)
        if max_score is None or override.final_score > max_score:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Final score is out of range"
            )
        answer.final_score = round(override.final_score, 2)
        answer.final_comment = override.final_comment or answer.ai_comment
        final_total += answer.final_score

    submission.status = SubmissionStatus.confirmed
    submission.final_total_score = round(final_total, 2)
    submission.reviewed_by = current_user.id
    submission.reviewed_at = datetime.now(UTC)
    await submission.save()
    return submission
