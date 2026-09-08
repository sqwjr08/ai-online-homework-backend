from typing import Annotated

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query
from pymongo import ReturnDocument

from app.core.deps import get_current_user, require_roles
from app.models import ClassGroup, User, UserRole, utc_now
from app.schemas import ClassCreate, ClassJoinRequest, ClassRead, UserPage, UserRead
from app.services.classes import create_class_group, join_class_group, teacher_class

router = APIRouter(prefix="/classes", tags=["classes"])
ACCESS_ERRORS = {
    401: {"description": "Not authenticated"},
    403: {"description": "Forbidden"},
    404: {"description": "Class not found"},
}


@router.post(
    "",
    response_model=ClassRead,
    status_code=201,
    responses={
        **ACCESS_ERRORS,
        400: {"description": "Active teacher required"},
        503: {"description": "Class code allocation failed; retry"},
    },
)
async def create_class(
    payload: ClassCreate,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
):
    return await create_class_group(
        payload.name, payload.teacher_id or current_user.id, current_user
    )


@router.post(
    "/join",
    response_model=ClassRead,
    responses={**ACCESS_ERRORS, 409: {"description": "Already belongs to another class"}},
)
async def join_class(
    payload: ClassJoinRequest, current_user: User = Depends(require_roles(UserRole.student))
):
    return await join_class_group(payload.code, current_user)


@router.get("/my", response_model=list[ClassRead], responses=ACCESS_ERRORS)
async def my_classes(current_user: User = Depends(get_current_user), is_active: bool | None = None):
    filters = {}
    if current_user.role == UserRole.teacher:
        filters["teacher_id"] = current_user.id
    elif current_user.role == UserRole.student:
        if not current_user.class_id:
            return []
        filters["_id"] = current_user.class_id
    if is_active is not None:
        filters["is_active"] = is_active
    return await ClassGroup.find(filters).sort("-created_at", "-_id").to_list()


@router.get("/{class_id}/members", response_model=UserPage, responses=ACCESS_ERRORS)
async def class_members(
    class_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    is_active: bool | None = None,
):
    await teacher_class(class_id, current_user)
    filters = {"class_id": class_id, "role": UserRole.student}
    if is_active is not None:
        filters["is_active"] = is_active
    total = await User.find(filters).count()
    members = (
        await User.find(filters)
        .sort("created_at", "_id")
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list()
    )
    return UserPage(
        items=[UserRead.model_validate(user, from_attributes=True) for user in members],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{class_id}/archive", response_model=ClassRead, responses=ACCESS_ERRORS)
async def archive_class(
    class_id: PydanticObjectId,
    current_user: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
):
    await teacher_class(class_id, current_user)
    filters = {"_id": class_id, "is_active": True}
    if current_user.role == UserRole.teacher:
        filters["teacher_id"] = current_user.id
    document = await ClassGroup.get_pymongo_collection().find_one_and_update(
        filters,
        {"$set": {"is_active": False, "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    return (
        ClassGroup.model_validate(document)
        if document
        else await teacher_class(class_id, current_user)
    )
