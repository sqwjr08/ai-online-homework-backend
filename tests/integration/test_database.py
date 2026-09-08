from datetime import datetime

import pytest
from beanie.odm.fields import PydanticObjectId
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, InvalidOperation

from app import db
from app.models import (
    Assignment,
    AssignmentStatus,
    ClassGroup,
    Question,
    Submission,
    User,
    UserRole,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def document_cases(test_app, account_password_hash):
    teacher_id, student_id, class_id, question_id, assignment_id = (
        PydanticObjectId() for _ in range(5)
    )
    return {
        User: (
            {
                "username": "db_student",
                "password_hash": account_password_hash,
                "role": UserRole.student,
                "class_id": class_id,
            },
            "is_active",
            False,
        ),
        ClassGroup: (
            {
                "name": "Database class",
                "code": "DBTEST",
                "teacher_id": teacher_id,
            },
            "name",
            "Updated class",
        ),
        Question: (
            {
                "prompt": "Explain pressure",
                "reference_answer": "Force per unit area",
                "max_score": 10,
                "rubric": "Definition and units",
                "image_urls": ["http://testserver/uploads/pressure.png"],
                "created_by": teacher_id,
            },
            "max_score",
            12.5,
        ),
        Assignment: (
            {
                "title": "Database assignment",
                "class_id": class_id,
                "questions": [{"question_id": question_id}],
                "created_by": teacher_id,
            },
            "status",
            AssignmentStatus.draft,
        ),
        Submission: (
            {
                "assignment_id": assignment_id,
                "student_id": student_id,
                "answers": [
                    {
                        "question_id": question_id,
                        "answer_text": "Force per unit area",
                        "ai_score": 8.5,
                        "ai_comment": "Check the units",
                    }
                ],
            },
            "final_total_score",
            9.5,
        ),
    }


async def test_async_database_connection(test_app, test_settings):
    assert isinstance(db.client, AsyncMongoClient)
    assert (await db.client.admin.command("ping"))["ok"] == 1
    for model in (User, ClassGroup, Question, Assignment, Submission):
        collection = model.get_pymongo_collection()
        assert collection.database.name == test_settings.database_name
        assert collection.database.client is db.client


@pytest.mark.parametrize("model", [User, ClassGroup, Question, Assignment, Submission])
async def test_document_insert_query_update_delete(document_cases, model):
    payload, updated_field, updated_value = document_cases[model]
    document = model(**payload)
    await document.insert()

    loaded = await model.get(document.id)
    assert loaded is not None
    # MongoDB stores datetimes at millisecond precision; compare the remaining business fields.
    timestamps = {"created_at", "updated_at", "submitted_at"}
    assert loaded.model_dump(exclude=timestamps) == document.model_dump(exclude=timestamps)

    setattr(loaded, updated_field, updated_value)
    await loaded.save()
    reloaded = await model.find_one({"_id": document.id, updated_field: updated_value})
    assert reloaded is not None
    assert getattr(reloaded, updated_field) == updated_value

    await reloaded.delete()
    assert await model.get(document.id) is None


@pytest.mark.parametrize("model,field", [(User, "username"), (ClassGroup, "code")])
async def test_unique_indexes_reject_duplicates(document_cases, model, field):
    collection = model.get_pymongo_collection()
    indexes = await collection.index_information()
    assert indexes[f"{field}_1"]["key"] == [(field, 1)]
    assert indexes[f"{field}_1"]["unique"] is True

    payload, _, _ = document_cases[model]
    await model(**payload).insert()
    with pytest.raises(DuplicateKeyError):
        await model(**payload).insert()
    assert await model.count() == 1


async def test_reconnect_preserves_data_and_existing_indexes(
    test_app, test_settings, account_password_hash
):
    user = User(
        username="persisted_user", password_hash=account_password_hash, role=UserRole.student
    )
    await user.insert()
    collection = User.get_pymongo_collection()
    await collection.create_index("created_at", name="test_preserved_created_at")
    indexes_before = await collection.index_information()
    previous_client = db.client

    await db.close_database()
    assert db.client is None
    with pytest.raises(InvalidOperation):
        await previous_client.admin.command("ping")
    await db.close_database()

    await db.init_database(test_settings)
    assert db.client is not previous_client
    assert (await db.client.admin.command("ping"))["ok"] == 1
    assert await User.get_pymongo_collection().index_information() == indexes_before
    assert (await User.get(user.id)).username == "persisted_user"


async def test_existing_user_document_remains_compatible(test_app, account_password_hash):
    collection = User.get_pymongo_collection()
    original = {
        "_id": PydanticObjectId(),
        "username": "existing_student",
        "password_hash": account_password_hash,
        "role": "student",
        "is_active": True,
        "class_id": PydanticObjectId(),
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 2),
    }
    await collection.insert_one(original)
    user = await User.get(original["_id"])
    assert user.role == UserRole.student
    assert user.class_id == original["class_id"]
    assert user.created_at == original["created_at"]
    user.username = "updated_student"
    await user.save()

    stored = await collection.find_one({"_id": original["_id"]})
    assert stored == {**original, "username": "updated_student", "token_version": 0}
