"""Run separately from uvicorn: python -m app.worker."""
import asyncio
import logging

from app.core.config import get_settings
from app.db import close_database, init_database
from app.services.grading import get_grading_service
from app.services.grading_worker import GradingWorker


async def main() -> None:
    settings = get_settings()
    if settings.ai_provider != "placeholder":
        raise ValueError("Only the placeholder AI provider is implemented")
    try:
        await init_database(settings)
        await GradingWorker(settings, get_grading_service()).run()
    finally:
        await close_database()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
