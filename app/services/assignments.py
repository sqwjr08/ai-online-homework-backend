from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError
from pymongo import ReturnDocument

from app.models import Assignment, AssignmentStatus, Question, QuestionSnapshot, User, UserRole, utc_now
from app.schemas import (
    AssignmentRead,
    StudentAssignmentRead,
    StudentAssignmentQuestionRead,
    TeacherAssignmentQuestionRead,
)


def as_utc(value: datetime) -> datetime:
    # PyMongo's default BSON decoding returns naive UTC, not local time.
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


def ensure_deadline_open(assignment: Assignment) -> None:
    if assignment.due_at is not None and as_utc(assignment.due_at) <= utc_now():
        raise HTTPException(status_code=400, detail="Assignment is closed")


async def validate_assignment_questions(ids: list, actor: User) -> None:
    if not ids:
        raise HTTPException(status_code=400, detail="At least one question is required")
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="Assignment questions must be unique")
    questions = await Question.find({"_id": {"$in": ids}, "is_active": True}).to_list()
    if len(questions) != len(ids):
        raise HTTPException(status_code=400, detail="Question not found")
    if actor.role == UserRole.teacher and any(q.created_by != actor.id for q in questions):
        raise HTTPException(status_code=403, detail="Cannot use another teacher's question")


async def update_assignment_atomically(assignment: Assignment, changes: dict) -> Assignment:
    filters = {"_id": assignment.id, "status": assignment.status}
    if assignment.revision == 0:
        filters["$or"] = [{"revision": 0}, {"revision": {"$exists": False}}]
    else:
        filters["revision"] = assignment.revision
    result = await Assignment.get_pymongo_collection().find_one_and_update(
        filters,
        {"$set": {**changes, "updated_at": utc_now()}, "$inc": {"revision": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=409, detail="Assignment changed; reload and retry")
    return Assignment.model_validate(result)


def snapshot_of(question: Question) -> QuestionSnapshot:
    try:
        return QuestionSnapshot.model_validate(question, from_attributes=True)
    except ValidationError as exc:
        raise HTTPException(status_code=409, detail="Question content requires repair") from exc


async def referenced_content(assignment: Assignment) -> list[QuestionSnapshot]:
    ids = [item.question_id for item in assignment.questions]
    questions = await Question.find({"_id": {"$in": ids}}).to_list()
    by_id = {question.id: question for question in questions}
    if len(by_id) != len(ids):
        raise HTTPException(status_code=409, detail="Assignment question references require repair")
    # Preserve author-specified order, not MongoDB's retrieval order. Legacy inactive items remain readable.
    return [snapshot_of(by_id[question_id]) for question_id in ids]


async def assignment_content(assignment: Assignment) -> tuple[str, list[QuestionSnapshot]]:
    if assignment.snapshot_version is not None:
        if (
            assignment.snapshot_version != 1
            or assignment.status not in {AssignmentStatus.published, AssignmentStatus.archived}
            or assignment.archived_from == AssignmentStatus.draft
            or not assignment.questions
            or any(item.snapshot is None for item in assignment.questions)
            or len({item.question_id for item in assignment.questions}) != len(assignment.questions)
        ):
            raise HTTPException(status_code=409, detail="Assignment snapshot requires repair")
        return "snapshot", [item.snapshot for item in assignment.questions]
    if any(item.snapshot is not None for item in assignment.questions):
        raise HTTPException(status_code=409, detail="Assignment snapshot version is missing")
    was_draft = assignment.status == AssignmentStatus.draft or assignment.archived_from == AssignmentStatus.draft
    source = "draft_preview" if was_draft else "legacy_reference"
    return source, await referenced_content(assignment)


async def assignment_read(assignment: Assignment, actor: User) -> AssignmentRead | StudentAssignmentRead:
    source, contents = await assignment_content(assignment)
    common = {
        "id": assignment.id,
        "title": assignment.title,
        "description": assignment.description,
        "class_id": assignment.class_id,
        "due_at": as_utc(assignment.due_at) if assignment.due_at is not None else None,
        "status": assignment.status,
        "created_by": assignment.created_by,
        "created_at": assignment.created_at,
        "question_source": source,
    }
    questions = []
    for position, (item, content) in enumerate(zip(assignment.questions, contents, strict=True), 1):
        public = {
            "question_id": item.question_id,
            "position": position,
            "prompt": content.prompt,
            "image_urls": content.image_urls,
            "max_score": content.max_score,
        }
        if actor.role == UserRole.student:
            questions.append(StudentAssignmentQuestionRead(**public))
        else:
            questions.append(TeacherAssignmentQuestionRead(
                **public, reference_answer=content.reference_answer, rubric=content.rubric,
            ))
    # Explicit models and a discriminated response union prevent secret fields leaking via union fallback.
    if actor.role == UserRole.student:
        return StudentAssignmentRead(**common, questions=questions)
    return AssignmentRead(**common, questions=questions)
