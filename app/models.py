from datetime import UTC, datetime
from enum import StrEnum

from beanie import Document, Indexed
from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class UserRole(StrEnum):
    admin = "admin"
    teacher = "teacher"
    student = "student"


class AssignmentStatus(StrEnum):
    draft = "draft"
    published = "published"


class SubmissionStatus(StrEnum):
    pending_teacher_review = "pending_teacher_review"
    confirmed = "confirmed"


class User(Document):
    username: Indexed(str, unique=True)
    password_hash: str
    token_version: int = Field(default=0, ge=0)
    role: UserRole
    is_active: bool = True
    class_id: PydanticObjectId | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "users"
        indexes = ["class_id"]


class ClassGroup(Document):
    name: str
    code: Indexed(str, unique=True)
    teacher_id: PydanticObjectId
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "classes"


class Question(Document):
    prompt: str
    reference_answer: str
    max_score: float = Field(gt=0)
    rubric: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    created_by: PydanticObjectId
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "questions"


class AssignmentQuestion(BaseModel):
    question_id: PydanticObjectId


class Assignment(Document):
    title: str
    description: str | None = None
    class_id: PydanticObjectId
    questions: list[AssignmentQuestion]
    due_at: datetime | None = None
    status: AssignmentStatus = AssignmentStatus.published
    created_by: PydanticObjectId
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "assignments"


class SubmissionAnswer(BaseModel):
    question_id: PydanticObjectId
    answer_text: str
    ai_score: float | None = None
    ai_comment: str | None = None
    final_score: float | None = None
    final_comment: str | None = None


class Submission(Document):
    assignment_id: PydanticObjectId
    student_id: PydanticObjectId
    answers: list[SubmissionAnswer]
    status: SubmissionStatus = SubmissionStatus.pending_teacher_review
    ai_total_score: float | None = None
    final_total_score: float | None = None
    submitted_at: datetime = Field(default_factory=utc_now)
    reviewed_by: PydanticObjectId | None = None
    reviewed_at: datetime | None = None

    class Settings:
        name = "submissions"
