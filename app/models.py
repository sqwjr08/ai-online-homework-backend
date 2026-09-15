from datetime import UTC, datetime
from enum import StrEnum

from beanie import Document, Indexed
from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class UserRole(StrEnum):
    admin = "admin"
    teacher = "teacher"
    student = "student"


class AssignmentStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class SubmissionStatus(StrEnum):
    pending_teacher_review = "pending_teacher_review"
    confirmed = "confirmed"


class AIGradingStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    legacy_unknown = "legacy_unknown"


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
    content_locked: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "questions"


class QuestionSnapshot(BaseModel):
    prompt: str
    reference_answer: str
    max_score: float = Field(gt=0, allow_inf_nan=False)
    rubric: str | None = None
    image_urls: list[str] = Field(default_factory=list)


class AssignmentQuestion(BaseModel):
    question_id: PydanticObjectId
    snapshot: QuestionSnapshot | None = None


class Assignment(Document):
    title: str
    description: str | None = None
    class_id: PydanticObjectId
    questions: list[AssignmentQuestion]
    snapshot_version: int | None = None
    due_at: datetime | None = None
    status: AssignmentStatus = AssignmentStatus.published
    archived_from: AssignmentStatus | None = None
    revision: int = Field(default=0, ge=0)
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
    # Missing state in historical documents must not enqueue an automatic regrade.
    ai_status: AIGradingStatus = AIGradingStatus.legacy_unknown
    ai_token: str | None = None
    ai_lease_until: datetime | None = None
    ai_attempts: int = Field(default=0, ge=0)
    ai_cycle_attempts: int | None = Field(default=None, ge=0)
    ai_retry_count: int = Field(default=0, ge=0)
    ai_next_attempt_at: datetime | None = None
    ai_error_code: str | None = None
    ai_total_score: float | None = None
    final_total_score: float | None = None
    submitted_at: datetime = Field(default_factory=utc_now)
    reviewed_by: PydanticObjectId | None = None
    reviewed_at: datetime | None = None

    class Settings:
        name = "submissions"
        indexes = [IndexModel(
            [("assignment_id", 1), ("student_id", 1)],
            unique=True, name="unique_assignment_student",
        ), IndexModel(
            [("status", 1), ("ai_status", 1), ("ai_lease_until", 1), ("submitted_at", 1)],
            name="ai_work_queue",
        )]
