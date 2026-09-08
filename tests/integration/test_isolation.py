import pytest

from app.models import User, UserRole

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("run", [1, 2])
async def test_each_case_gets_an_empty_database(
    api_client, test_settings, account_password_hash, run
):
    database = User.get_pymongo_collection().database
    assert database.name == test_settings.database_name
    assert await User.count() == 0
    await User(
        username="same_username_in_each_case",
        password_hash=account_password_hash,
        role=UserRole.student,
    ).insert()
    assert await User.count() == 1


async def test_app_uses_temporary_upload_directory(test_app, test_settings, tmp_path):
    assert test_settings.upload_dir == tmp_path / "uploads"
    upload_mount = next(route for route in test_app.routes if route.path == "/uploads")
    assert upload_mount.app.directory == test_settings.upload_dir
