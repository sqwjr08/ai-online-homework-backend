import re
from typing import Annotated
from beanie import PydanticObjectId

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import require_roles
from app.models import User, UserRole
from app.schemas import UserCreate, UserRead, UserPage, UserStatusUpdate, PasswordReset
from app.services.users import create_account, set_account_status, reset_account_password

router = APIRouter(prefix="/users", tags=["users"])

MANAGEMENT_ERRORS = {
    401: {"description": "Not authenticated or token revoked"},
    403: {"description": "Administrator required or protected account"},
    404: {"description": "User not found"},
}


@router.patch("/{user_id}/status", response_model=UserRead, responses=MANAGEMENT_ERRORS)
async def update_user_status(
    user_id: PydanticObjectId,
    payload: UserStatusUpdate,
    _: User = Depends(require_roles(UserRole.admin)),
) -> User:
    return await set_account_status(user_id, payload.is_active)


@router.post(
    "/{user_id}/reset-password",
    response_model=UserRead,
    responses={**MANAGEMENT_ERRORS, 409: {"description": "Account changed; retry"}},
)
async def reset_user_password(
    user_id: PydanticObjectId,
    payload: PasswordReset,
    _: User = Depends(require_roles(UserRole.admin)),
) -> User:
    return await reset_account_password(user_id, payload.password)


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Cannot create an administrator"},
        401: {"description": "Not authenticated"},
        403: {"description": "Administrator required"},
        409: {"description": "Username already exists"},
    },
)
async def create_user(
    payload: UserCreate,
    _: User = Depends(require_roles(UserRole.admin)),
) -> User:
    if payload.role == UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot create admin here"
        )
    return await create_account(
        payload.username,
        payload.password,
        payload.role,
        is_active=payload.is_active,
    )


@router.get(
    "",
    response_model=UserPage,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Administrator required"},
    },
)
async def list_users(
    _: User = Depends(require_roles(UserRole.admin)),
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    role: UserRole | None = None,
    is_active: bool | None = None,
    username: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
) -> UserPage:
    filters = {}
    if role is not None:
        filters["role"] = role
    if is_active is not None:
        filters["is_active"] = is_active
    if username is not None:
        filters["username"] = {"$regex": re.escape(username), "$options": "i"}
    total = await User.find(filters).count()
    users = (
        await User.find(filters)
        .sort("-created_at", "-_id")
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list()
    )
    return UserPage(
        items=[UserRead.model_validate(user, from_attributes=True) for user in users],
        total=total,
        page=page,
        page_size=page_size,
    )
