from beanie import PydanticObjectId
from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from app.core.security import hash_password
from app.models import User, UserRole, utc_now


async def create_account(
    username: str, password: str, role: UserRole, *, is_active: bool = True
) -> User:
    if await User.find_one(User.username == username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    user = User(
        username=username,
        password_hash=await run_in_threadpool(hash_password, password),
        role=role,
        is_active=is_active,
    )
    try:
        await user.insert()
    except DuplicateKeyError as exc:
        # The unique index also arbitrates concurrent registration and admin creation.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already exists"
        ) from exc
    return user


async def manageable_user(user_id: PydanticObjectId) -> User:
    user = await User.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.admin:
        raise HTTPException(status_code=403, detail="Administrator accounts are protected")
    return user


async def set_account_status(user_id: PydanticObjectId, is_active: bool) -> User:
    await manageable_user(user_id)
    document = await User.get_pymongo_collection().find_one_and_update(
        {
            "_id": user_id,
            "role": {"$in": [UserRole.teacher, UserRole.student]},
            "is_active": {"$ne": is_active},
        },
        {"$set": {"is_active": is_active, "updated_at": utc_now()}, "$inc": {"token_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    # Repeating the same state is a no-op, not a new session revocation.
    return User.model_validate(document) if document else await manageable_user(user_id)


async def reset_account_password(user_id: PydanticObjectId, password: str) -> User:
    await manageable_user(user_id)
    password_hash = await run_in_threadpool(hash_password, password)
    document = await User.get_pymongo_collection().find_one_and_update(
        {"_id": user_id, "role": {"$in": [UserRole.teacher, UserRole.student]}},
        {
            "$set": {"password_hash": password_hash, "updated_at": utc_now()},
            "$inc": {"token_version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        await manageable_user(user_id)
        raise HTTPException(status_code=409, detail="Account changed; retry")
    return User.model_validate(document)
