from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.core.deps import get_current_user
from app.core.security import create_access_token, verify_and_update_password
from app.models import User, UserRole
from app.schemas import LoginRequest, TokenResponse, StudentRegister, UserRead
from app.services.users import create_account

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    user = await User.find_one(User.username == payload.username)
    valid, updated_hash = (False, None)
    if user:
        valid, updated_hash = await run_in_threadpool(
            verify_and_update_password, payload.password, user.password_hash
        )
    if not user or not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
    if updated_hash:
        # Do not overwrite a concurrent password reset or a disabled account.
        result = await User.find_one(
            {
                "_id": user.id,
                "password_hash": user.password_hash,
                "is_active": True,
                "$or": [
                    {"token_version": user.token_version},
                    {"token_version": {"$exists": False}},
                ]
                if user.token_version == 0
                else [{"token_version": user.token_version}],
            }
        ).update({"$set": {"password_hash": updated_hash}})
        if result.matched_count != 1:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    current = await User.get(user.id)
    if (
        not current
        or not current.is_active
        or current.token_version != user.token_version
        or current.password_hash != (updated_hash or user.password_hash)
    ):
        raise HTTPException(
            status_code=401,
            detail="Credentials changed; please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Keep the verified version: a reset after this read must revoke this token too.
    token = create_access_token(
        str(user.id), {"role": current.role.value}, token_version=user.token_version
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Username already exists"}},
)
async def register_student(payload: StudentRegister) -> User:
    return await create_account(
        payload.username,
        payload.password,
        UserRole.student,
    )


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"message": "Client should discard the bearer token."}
