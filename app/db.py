from beanie import init_beanie
from pymongo import AsyncMongoClient
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.security import hash_password
from app.models import Assignment, ClassGroup, Question, Submission, User, UserRole

client: AsyncMongoClient | None = None


async def init_database(settings: Settings) -> None:
    global client
    client = AsyncMongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    await init_beanie(
        database=client[settings.database_name],
        document_models=[User, ClassGroup, Question, Assignment, Submission],
        allow_index_dropping=False,
    )
    await ensure_dev_admin(settings)


async def close_database() -> None:
    global client
    if client is not None:
        await client.close()
        client = None


async def ensure_dev_admin(settings: Settings) -> None:
    if settings.environment != "development" or not settings.enable_dev_default_admin:
        return
    existing = await User.find_one(User.username == settings.dev_admin_username)
    if existing:
        return
    await User(
        username=settings.dev_admin_username,
        password_hash=await run_in_threadpool(hash_password, settings.dev_admin_password),
        role=UserRole.admin,
    ).insert()
