from beanie import PydanticObjectId
from fastapi import HTTPException
from pymongo import ReturnDocument

from app.models import Assignment, Question, User, UserRole, utc_now
from app.schemas import QuestionUpdate


async def teacher_question(question_id: PydanticObjectId, actor: User) -> Question:
    question = await Question.get(question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    if actor.role != UserRole.admin and not (
        actor.role == UserRole.teacher and question.created_by == actor.id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return question


async def protect_legacy_reference(question: Question) -> None:
    # Older assignments predate content_locked. Never change their live question references.
    if question.content_locked or await Assignment.find_one({"questions.question_id": question.id}):
        await Question.get_pymongo_collection().update_one(
            {"_id": question.id}, {"$set": {"content_locked": True}}
        )
        raise HTTPException(status_code=409, detail="Question content is locked; create a new question")


async def change_question(
    question_id: PydanticObjectId, actor: User, payload: QuestionUpdate | None = None,
) -> Question:
    question = await teacher_question(question_id, actor)
    if not question.is_active:
        if payload is None:
            return question
        raise HTTPException(status_code=409, detail="Question is disabled")
    await protect_legacy_reference(question)
    changes = payload.model_dump(exclude_unset=True) if payload is not None else {"is_active": False}
    filters = {"_id": question_id, "is_active": True, "content_locked": {"$ne": True}}
    if actor.role == UserRole.teacher:
        filters["created_by"] = actor.id
    document = await Question.get_pymongo_collection().find_one_and_update(
        filters,
        {"$set": {**changes, "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if document:
        return Question.model_validate(document)
    current = await teacher_question(question_id, actor)
    if payload is None and not current.is_active:
        return current
    raise HTTPException(status_code=409, detail="Question changed or content locked; reload and retry")


async def lock_assignment_questions(question_ids: list[PydanticObjectId], actor: User) -> None:
    # A permanent, single-document lock arbitrates edits/disables against assignment creation.
    # Do not roll back locks on insert failure: another assignment may already rely on them.
    for question_id in sorted(set(question_ids)):
        filters = {"_id": question_id, "is_active": True}
        if actor.role == UserRole.teacher:
            filters["created_by"] = actor.id
        result = await Question.get_pymongo_collection().update_one(
            filters, {"$set": {"content_locked": True}}
        )
        if not result.matched_count:
            raise HTTPException(status_code=409, detail="Question changed; reload and retry")
