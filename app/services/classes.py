import secrets
import string

from beanie import PydanticObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.models import ClassGroup, User, UserRole, utc_now


async def teacher_class(class_id: PydanticObjectId, user: User) -> ClassGroup:
    group = await ClassGroup.get(class_id)
    if not group:
        raise HTTPException(status_code=404, detail="Class not found")
    if user.role != UserRole.admin and not (
        user.role == UserRole.teacher and group.teacher_id == user.id
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return group


async def create_class_group(name: str, teacher_id: PydanticObjectId, actor: User) -> ClassGroup:
    if actor.role == UserRole.teacher and teacher_id != actor.id:
        raise HTTPException(status_code=403, detail="Teacher can only use self")
    teacher = await User.get(teacher_id)
    if not teacher or teacher.role != UserRole.teacher or not teacher.is_active:
        raise HTTPException(status_code=400, detail="Active teacher required")
    for _ in range(10):
        code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        group = ClassGroup(name=name, code=code, teacher_id=teacher_id)
        try:
            await group.insert()
            return group
        except DuplicateKeyError:
            continue
    raise HTTPException(status_code=503, detail="Cannot allocate class code; retry")


async def join_class_group(code: str, student: User) -> ClassGroup:
    group = await ClassGroup.find_one(ClassGroup.code == code)
    if not group or not group.is_active:
        raise HTTPException(status_code=404, detail="Active class not found")
    version = (
        {"$or": [{"token_version": 0}, {"token_version": {"$exists": False}}]}
        if student.token_version == 0
        else {"token_version": student.token_version}
    )
    result = await User.get_pymongo_collection().update_one(
        {
            "_id": student.id,
            "role": UserRole.student,
            "is_active": True,
            "class_id": None,
            **version,
        },
        {"$set": {"class_id": group.id, "updated_at": utc_now()}},
    )
    if result.matched_count:
        return group
    current = await User.get(student.id)
    if (
        not current
        or not current.is_active
        or current.role != UserRole.student
        or current.token_version != student.token_version
    ):
        raise HTTPException(
            status_code=401,
            detail="Credentials changed; log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if current.class_id == group.id:
        return group
    raise HTTPException(status_code=409, detail="Student already belongs to another class")
