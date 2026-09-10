import math
from decimal import Decimal

from fastapi import HTTPException
from pymongo import ReturnDocument

from app.models import Assignment, AssignmentStatus, Submission, SubmissionStatus, User, UserRole, utc_now
from app.schemas import (
    ConfirmGradeRequest, StudentSubmissionAnswerRead, StudentSubmissionRead,
    SubmissionPage, SubmissionRead,
)
from app.services.assignments import assignment_content


def submission_read(submission: Submission, actor: User) -> SubmissionRead | StudentSubmissionRead:
    if actor.role != UserRole.student:
        return SubmissionRead.model_validate(submission)
    confirmed = submission.status == SubmissionStatus.confirmed
    return StudentSubmissionRead(
        id=submission.id, assignment_id=submission.assignment_id, student_id=submission.student_id,
        status=submission.status, submitted_at=submission.submitted_at,
        final_total_score=submission.final_total_score if confirmed else None,
        reviewed_by=submission.reviewed_by if confirmed else None,
        reviewed_at=submission.reviewed_at if confirmed else None,
        answers=[StudentSubmissionAnswerRead(
            question_id=item.question_id, answer_text=item.answer_text,
            final_score=item.final_score if confirmed else None,
            final_comment=item.final_comment if confirmed else None,
        ) for item in submission.answers],
    )


async def submission_page(assignment: Assignment, page: int, page_size: int,
                          status: SubmissionStatus | None) -> SubmissionPage:
    filters = {"status": status} if status is not None else {}
    cursor = await Submission.get_pymongo_collection().aggregate([
        {"$match": {"assignment_id": assignment.id}},
        {"$facet": {
            "items": [{"$match": filters}, {"$sort": {"submitted_at": 1, "_id": 1}},
                      {"$skip": (page - 1) * page_size}, {"$limit": page_size}],
            "total": [{"$match": filters}, {"$count": "count"}],
            "progress": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}],
        }},
    ])
    async with cursor:
        result = (await cursor.to_list(length=1))[0]
    counts = {item["_id"]: item["count"] for item in result["progress"]}
    return SubmissionPage(
        items=[SubmissionRead.model_validate(Submission.model_validate(item)) for item in result["items"]],
        total=result["total"][0]["count"] if result["total"] else 0,
        page=page, page_size=page_size,
        progress={"submitted_count": sum(counts.values()),
                  "pending_count": counts.get(SubmissionStatus.pending_teacher_review, 0),
                  "confirmed_count": counts.get(SubmissionStatus.confirmed, 0)},
    )


async def confirm_submission_grade(submission: Submission, assignment: Assignment,
                                   payload: ConfirmGradeRequest, actor: User) -> Submission:
    if submission.status != SubmissionStatus.pending_teacher_review:
        raise HTTPException(status_code=409, detail="Grade already confirmed and locked")
    if assignment.status == AssignmentStatus.draft or assignment.archived_from == AssignmentStatus.draft:
        raise HTTPException(status_code=409, detail="Assignment was not published")
    ids = [item.question_id for item in assignment.questions]
    answer_ids = [item.question_id for item in submission.answers]
    if (not ids or len(set(ids)) != len(ids) or len(set(answer_ids)) != len(answer_ids)
            or set(ids) != set(answer_ids)):
        raise HTTPException(status_code=409, detail="Submission or assignment questions require repair")
    grade_ids = [item.question_id for item in payload.grades]
    if len(set(grade_ids)) != len(grade_ids) or set(grade_ids) != set(answer_ids):
        raise HTTPException(status_code=400, detail="Grades must match submission answers exactly once")
    _, contents = await assignment_content(assignment)
    max_scores = dict(zip(ids, [content.max_score for content in contents], strict=True))
    grades = {item.question_id: item for item in payload.grades}
    answers = []
    total = Decimal(0)
    for answer in submission.answers:
        grade = grades[answer.question_id]
        if grade.final_score > max_scores[answer.question_id]:
            raise HTTPException(status_code=400, detail="Final score is out of range")
        total += Decimal(str(grade.final_score))
        answers.append(answer.model_copy(update={
            "final_score": grade.final_score, "final_comment": grade.final_comment,
        }).model_dump())
    final_total = float(total)
    if not math.isfinite(final_total):
        raise HTTPException(status_code=400, detail="Final total score is out of range")
    # Only the pending -> confirmed transition may write grades, including under concurrent review.
    stored = await Submission.get_pymongo_collection().find_one_and_update(
        {"_id": submission.id, "status": SubmissionStatus.pending_teacher_review},
        {"$set": {"answers": answers, "final_total_score": final_total,
                  "status": SubmissionStatus.confirmed, "reviewed_by": actor.id, "reviewed_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if stored is None:
        raise HTTPException(status_code=409, detail="Grade already confirmed or submission changed")
    return Submission.model_validate(stored)
