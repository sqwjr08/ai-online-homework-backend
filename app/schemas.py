import unicodedata
from decimal import Decimal
from datetime import UTC, datetime
from typing import Annotated, Literal

from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import AIGradingStatus, AssignmentStatus, SubmissionStatus, UserRole


Username = Annotated[str, Field(min_length=3, max_length=50)]
Password = Annotated[str, Field(min_length=4, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ImageUploadRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


class LoginRequest(BaseModel):
    username: Username
    password: Password


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username
    password: Password

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("Username cannot contain whitespace or control characters")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password cannot be blank")
        return value


class StudentRegister(AccountInput):
    role: Literal[UserRole.student] = UserRole.student


class UserCreate(AccountInput):
    role: UserRole
    is_active: bool = True


class UserRead(BaseModel):
    id: PydanticObjectId
    username: str
    role: UserRole
    is_active: bool
    class_id: PydanticObjectId | None
    created_at: datetime


class UserPage(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int


class UserStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: Annotated[bool, Field(strict=True)]


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: Password

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return AccountInput.validate_password(value)


class ClassCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(min_length=1, max_length=100)]
    teacher_id: PydanticObjectId | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ClassRead(BaseModel):
    id: PydanticObjectId
    name: str
    code: str
    teacher_id: PydanticObjectId
    is_active: bool
    created_at: datetime


class ClassJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, Field(pattern=r"^[A-Z0-9]{6}$")]

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


QuestionPrompt = Annotated[str, Field(min_length=1, max_length=10_000)]
QuestionAnswer = Annotated[str, Field(min_length=1, max_length=20_000)]
QuestionScore = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
QuestionRubric = Annotated[str | None, Field(min_length=1, max_length=5_000)]


class QuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: QuestionPrompt
    reference_answer: QuestionAnswer
    max_score: QuestionScore
    rubric: QuestionRubric = None
    image_urls: list[str] = Field(default_factory=list)

    @field_validator("prompt", "reference_answer", "rubric", mode="before")
    @classmethod
    def normalize_question_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class QuestionUpdate(QuestionCreate):
    prompt: QuestionPrompt | None = None
    reference_answer: QuestionAnswer | None = None
    max_score: QuestionScore | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "QuestionUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one question field is required")
        for name in ("prompt", "reference_answer", "max_score"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class QuestionRead(BaseModel):
    id: PydanticObjectId
    prompt: str
    reference_answer: str
    max_score: float
    rubric: str | None
    image_urls: list[str]
    created_by: PydanticObjectId
    is_active: bool
    created_at: datetime
    updated_at: datetime


class QuestionPage(BaseModel):
    items: list[QuestionRead]
    total: int
    page: int
    page_size: int


class AssignmentQuestionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: PydanticObjectId


class AssignmentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None
    questions: list[AssignmentQuestionCreate]
    due_at: datetime | None = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_at")
    @classmethod
    def normalize_deadline(cls, value):
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("due_at must include a timezone")
        value = value.astimezone(UTC)
        return value.replace(microsecond=value.microsecond // 1000 * 1000)


class AssignmentCreate(AssignmentFields):
    class_id: PydanticObjectId
    status: Literal[AssignmentStatus.draft, AssignmentStatus.published] = AssignmentStatus.published


class AssignmentUpdate(AssignmentFields):
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    questions: list[AssignmentQuestionCreate] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "AssignmentUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one assignment field is required")
        for name in ("title", "questions"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class StudentAssignmentQuestionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: PydanticObjectId
    position: int
    prompt: str
    image_urls: list[str]
    max_score: float


class TeacherAssignmentQuestionRead(StudentAssignmentQuestionRead):
    reference_answer: str
    rubric: str | None


class AssignmentBaseRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: PydanticObjectId
    title: str
    description: str | None
    class_id: PydanticObjectId
    due_at: datetime | None
    status: AssignmentStatus
    created_by: PydanticObjectId
    created_at: datetime
    question_source: Literal["snapshot", "draft_preview", "legacy_reference"]


class AssignmentRead(AssignmentBaseRead):
    view: Literal["teacher"] = "teacher"
    questions: list[TeacherAssignmentQuestionRead]


class StudentAssignmentRead(AssignmentBaseRead):
    view: Literal["student"] = "student"
    questions: list[StudentAssignmentQuestionRead]


AssignmentResponse = Annotated[
    AssignmentRead | StudentAssignmentRead, Field(discriminator="view")
]


class SubmissionAnswerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: PydanticObjectId
    answer_text: Annotated[str, Field(min_length=1, max_length=20_000)]

    @field_validator("answer_text")
    @classmethod
    def validate_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Answer cannot be blank")
        return value


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[SubmissionAnswerCreate] = Field(min_length=1)


class SubmissionAnswerRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    question_id: PydanticObjectId
    answer_text: str
    ai_score: float | None = None
    ai_comment: str | None = None
    final_score: float | None = None
    final_comment: str | None = None


class SubmissionRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    view: Literal["teacher"] = "teacher"
    id: PydanticObjectId
    assignment_id: PydanticObjectId
    student_id: PydanticObjectId
    answers: list[SubmissionAnswerRead]
    status: SubmissionStatus
    ai_status: AIGradingStatus
    ai_attempts: int = 0
    ai_retry_count: int = 0
    ai_next_attempt_at: datetime | None = None
    ai_error_code: str | None = None
    ai_total_score: float | None
    final_total_score: float | None
    submitted_at: datetime
    reviewed_by: PydanticObjectId | None
    reviewed_at: datetime | None


class RetryGradingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_retry_count: int = Field(ge=0, le=2_147_483_647, strict=True)


class StudentSubmissionAnswerRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: PydanticObjectId
    answer_text: str
    final_score: float | None = None
    final_comment: str | None = None


class StudentSubmissionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal["student"] = "student"
    id: PydanticObjectId
    assignment_id: PydanticObjectId
    student_id: PydanticObjectId
    answers: list[StudentSubmissionAnswerRead]
    status: SubmissionStatus
    final_total_score: float | None
    submitted_at: datetime
    reviewed_by: PydanticObjectId | None
    reviewed_at: datetime | None


SubmissionResponse = Annotated[
    SubmissionRead | StudentSubmissionRead, Field(discriminator="view")
]


class SubmissionProgress(BaseModel):
    submitted_count: int
    pending_count: int
    confirmed_count: int


class SubmissionPage(BaseModel):
    items: list[SubmissionRead]
    total: int
    page: int
    page_size: int
    progress: SubmissionProgress


class GradeOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: PydanticObjectId
    final_score: Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
    final_comment: Annotated[str | None, Field(max_length=1000)] = None

    @field_validator("final_score")
    @classmethod
    def validate_score_precision(cls, value: float) -> float:
        if Decimal(str(value)).as_tuple().exponent < -2:
            raise ValueError("Final score must have at most two decimal places")
        return value

    @field_validator("final_comment")
    @classmethod
    def normalize_final_comment(cls, value):
        return (value.strip() or None) if value is not None else None


class ConfirmGradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grades: list[GradeOverride] = Field(min_length=1)
