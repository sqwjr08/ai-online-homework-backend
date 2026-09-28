import asyncio
import json
import logging

import httpx
import pytest

from app.services.bailian import BailianGradingService
from app.services.grading_trace import (
    call_context, configure_call_logging, grading_context, logger,
)
from tests import test_bailian_adapter as helpers

settings = helpers.settings
question = helpers.question


async def test_call_metadata_and_privacy(settings, question, caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    payload = {**helpers.envelope(), "usage": {"prompt_tokens": 31, "completion_tokens": 9},
               "private": "SECRET_RESPONSE"}
    service = BailianGradingService(settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, headers={"x-request-id": "req-123"})))
    try:
        with grading_context("submission-1", "question-1", 2):
            await service.grade_short_answer(question, "SECRET_ANSWER")
        event = json.loads(caplog.records[-1].message)
        assert event["input_tokens"] == 31 and event["output_tokens"] == 9
        assert event["request_id"] == "req-123" and event["status"] == "succeeded"
        assert event["submission_id"] == "submission-1" and event["attempt"] == 2
        assert event["duration_ms"] >= 0 and event["started_at"]
        assert call_context.get() is None
        for private in ["SECRET_ANSWER", "SECRET_RESPONSE", "test-secret-only", "Example feedback"]:
            assert private not in caplog.text
    finally:
        await service.aclose()


async def test_failure_cancel_and_unknown_usage(settings, question, caplog):
    caplog.set_level(logging.INFO, logger=logger.name)
    for mode, expected in [("auth", "provider_authentication"), ("timeout", "timeout"),
                           ("cancel", "cancelled"), ("invalid", "invalid_provider_response")]:
        async def handler(request):
            if mode == "timeout":
                raise httpx.ReadTimeout("SECRET_ERROR")
            if mode == "cancel":
                raise asyncio.CancelledError("SECRET_ERROR")
            if mode == "auth":
                return httpx.Response(401, text="SECRET_ERROR")
            return httpx.Response(200, json={**helpers.envelope("invalid"),
                                            "usage": {"prompt_tokens": True, "completion_tokens": -1}})
        service = BailianGradingService(settings, transport=httpx.MockTransport(handler))
        before = len(caplog.records)
        try:
            with pytest.raises((Exception, asyncio.CancelledError)):
                await service.grade_short_answer(question, "SECRET_ANSWER")
            assert len(caplog.records) == before + 1
            event = json.loads(caplog.records[-1].message)
            assert event["error_code"] == expected
            assert event["input_tokens"] is None and event["output_tokens"] is None
            assert "SECRET_" not in caplog.text
        finally:
            await service.aclose()


def test_rotation_and_logging_failure_is_nonfatal(tmp_path, monkeypatch):
    from app.services.grading_trace import emit_call

    old_level, old_propagate = logger.level, logger.propagate
    handler = configure_call_logging(tmp_path)
    try:
        assert handler.maxBytes == 5 * 1024 * 1024 and handler.backupCount == 3
        handler.maxBytes = 100
        for index in range(20):
            emit_call({"event": "ai_call", "index": index, "status": "succeeded"})
        files = list(tmp_path.glob("ai-calls.jsonl*"))
        assert len(files) == 4
        for file in files:
            for line in file.read_text(encoding="utf-8").splitlines():
                assert json.loads(line)["event"] == "ai_call"

        def broken(*args, **kwargs):
            raise OSError("SECRET_DISK_ERROR")

        monkeypatch.setattr(logger, "info", broken)
        emit_call({"event": "ai_call"})
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(old_level)
        logger.propagate = old_propagate
