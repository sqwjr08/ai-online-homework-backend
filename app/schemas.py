import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from beanie.odm.fields import PydanticObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AssignmentStatus, SubmissionStatus, UserRole


Username = Annotated[str, Field(min_length=3, max_length=50)]
Password = Annotated[str, Field(min_length=4, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


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


class QuestionCreate(BaseModel):
    prompt: Annotated[str, Field(min_length=1)]
    reference_answer: Annotated[str, Field(min_length=1)]
    max_score: Annotated[float, Field(gt=0)]
    rubric: str | None = None
    image_urls: list[str] = Field(default_factory=list)


class QuestionRead(BaseModel):
    id: PydanticObjectId
    prompt: str
    reference_answer: str
    max_score: float
    rubric: str | None
    image_urls: list[str]
    created_by: PydanticObjectId
    created_at: datetime


class AssignmentQuestionCreate(BaseModel):
    question_id: PydanticObjectId


class AssignmentCreate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None
    class_id: PydanticObjectId
    questions: list[AssignmentQuestionCreate]
    due_at: datetime | None = None
    status: AssignmentStatus = AssignmentStatus.published


class AssignmentRead(BaseModel):
    id: PydanticObjectId
    title: str
    description: str | None
    class_id: PydanticObjectId
    questions: list[AssignmentQuestionCreate]
    due_at: datetime | None
    status: AssignmentStatus
    created_by: PydanticObjectId
    created_at: datetime


class SubmissionAnswerCreate(BaseModel):
    question_id: PydanticObjectId
    answer_text: Annotated[str, Field(min_length=1)]


class SubmissionCreate(BaseModel):
    answers: list[SubmissionAnswerCreate]


class SubmissionAnswerRead(BaseModel):
    question_id: PydanticObjectId
    answer_text: str
    ai_score: float | None = None
    ai_comment: str | None = None
    final_score: float | None = None
    final_comment: str | None = None


class SubmissionRead(BaseModel):
    id: PydanticObjectId
    assignment_id: PydanticObjectId
    student_id: PydanticObjectId
    answers: list[SubmissionAnswerRead]
    status: SubmissionStatus
    ai_total_score: float | None
    final_total_score: float | None
    submitted_at: datetime
    reviewed_by: PydanticObjectId | None
    reviewed_at: datetime | None


class GradeOverride(BaseModel):
    question_id: PydanticObjectId
    final_score: Annotated[float, Field(ge=0)]
    final_comment: str | None = None


class ConfirmGradeRequest(BaseModel):
    grades: list[GradeOverride]
