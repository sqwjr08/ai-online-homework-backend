"""Run separately from uvicorn: python -m app.worker."""
import asyncio
import logging

from app.core.config import get_settings
from app.db import close_database, init_database
from app.services.grading import get_grading_service
from app.services.grading_worker import GradingWorker
from app.services.grading_trace import configure_call_logging, logger as call_logger


async def main() -> None:
    settings = get_settings()
    grading_service = get_grading_service()
    trace_handler = None
    try:
        if settings.ai_provider == "bailian":
            trace_handler = configure_call_logging()
        await init_database(settings)
        await GradingWorker(settings, grading_service).run()
    finally:
        try:
            await grading_service.aclose()
        finally:
            try:
                await close_database()
            finally:
                if trace_handler is not None:
                    call_logger.removeHandler(trace_handler)
                    trace_handler.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
