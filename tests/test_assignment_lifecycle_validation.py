from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import AssignmentCreate, AssignmentUpdate


@pytest.mark.parametrize("payload", [
    {}, {"title": "  "}, {"title": None}, {"questions": None},
    {"due_at": "2030-01-01T12:00:00"}, {"class_id": "012345678901234567890123"},
    {"status": "published"}, {"snapshot_version": 1},
])
def test_invalid_draft_patch(payload):
    with pytest.raises(ValidationError):
        AssignmentUpdate.model_validate(payload)


def test_patch_preserves_omissions_and_normalizes_timezone():
    patch = AssignmentUpdate(title="  Work  ", due_at="2030-01-01T12:00:00.123456+08:00")
    assert patch.model_dump(exclude_unset=True) == {
        "title": "Work", "due_at": datetime(2030, 1, 1, 4, 0, 0, 123000, tzinfo=UTC),
    }
    assert AssignmentUpdate(description=None, due_at=None).model_dump(exclude_unset=True) == {
        "description": None, "due_at": None,
    }


def test_cannot_create_archived_assignment():
    with pytest.raises(ValidationError):
        AssignmentCreate(title="Work", class_id="012345678901234567890123", questions=[], status="archived")
