from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.db import close_database, init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        await init_database(settings)
        yield
    finally:
        await close_database()


async def health() -> dict[str, str]:
    return {"status": "ok"}


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    application.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
    application.include_router(api_router)
    application.add_api_route("/health", health, methods=["GET"], tags=["health"])
    return application


app = create_app()
