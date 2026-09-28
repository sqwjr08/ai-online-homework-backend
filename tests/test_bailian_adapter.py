import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import Settings
from app.models import QuestionSnapshot
from app.services.bailian import BailianGradingService, MAX_ENVELOPE_BYTES
from app.services.grading import (
    PermanentGradingError, PlaceholderGradingService, TransientGradingError, get_grading_service,
)
from app.services.grading_worker import GradingWorker


@pytest.fixture
def settings(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(_env_file=None, ai_provider="bailian", bailian_api_key="test-secret-only")


@pytest.fixture
def question():
    return QuestionSnapshot(prompt="Example", reference_answer="Example", max_score=10,
                            image_urls=["https://private.example/image.png"])


def envelope(content='{"score":8,"comment":"Example feedback"}', finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}]}


async def test_request_contract_and_success(settings, question):
    calls = []

    async def handler(request):
        calls.append(request)
        assert str(request.url) == settings.bailian_base_url + "/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret-only"
        body = json.loads(request.content)
        assert body["model"] == "qwen3.8-flash" and body["stream"] is False
        assert body["enable_thinking"] is False and body["max_tokens"] == 512
        assert body["response_format"] == {"type": "json_object"}
        assert "private.example" not in request.content.decode()
        return httpx.Response(200, json=envelope())

    service = BailianGradingService(settings, transport=httpx.MockTransport(handler))
    try:
        result = await service.grade_short_answer(question, "Example answer")
        assert result.score == 8 and len(calls) == 1
    finally:
        await service.aclose()
    assert service.client.is_closed


async def test_status_classification_without_hidden_retries(settings, question):
    for status in [408, 429, 500, 502, 503, 504, 400, 401, 403, 404, 302]:
        calls = []

        async def handler(request):
            calls.append(request)
            return httpx.Response(status, text="SECRET_RESPONSE", headers={"Location": "https://evil.example"})

        service = BailianGradingService(settings, transport=httpx.MockTransport(handler))
        expected = TransientGradingError if status in {408, 429, 500, 502, 503, 504} else PermanentGradingError
        try:
            with pytest.raises(expected) as exc:
                await service.grade_short_answer(question, "answer")
            assert "SECRET" not in str(exc.value) and "test-secret-only" not in str(exc.value)
            assert len(calls) == 1
            if status in {401, 403}:
                assert exc.value.code == "provider_authentication"
        finally:
            await service.aclose()


async def test_invalid_responses_never_become_grades(settings, question):
    refused = envelope()
    refused["choices"][0]["message"]["refusal"] = "No"
    for response in [httpx.Response(200, text="not json"),
                     httpx.Response(200, json=envelope(finish="length")),
                     httpx.Response(200, json=refused),
                     httpx.Response(200, json={"choices": []}),
                     httpx.Response(200, json=envelope('{"score":11,"comment":"bad"}')),
                     httpx.Response(200, json=envelope(None)),
                     httpx.Response(200, content=b"x" * (MAX_ENVELOPE_BYTES + 1))]:
        service = BailianGradingService(settings, transport=httpx.MockTransport(lambda _: response))
        try:
            with pytest.raises(PermanentGradingError, match="^invalid_provider_response$"):
                await service.grade_short_answer(question, "answer")
        finally:
            await service.aclose()


async def test_network_timeout_and_cancellation(settings, question):
    for error, expected in [(httpx.ReadTimeout("secret"), TimeoutError),
                            (httpx.ConnectError("secret"), TransientGradingError),
                            (asyncio.CancelledError(), asyncio.CancelledError)]:
        async def handler(_):
            raise error

        service = BailianGradingService(settings, transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(expected):
                await service.grade_short_answer(question, "answer")
        finally:
            await service.aclose()
    async def slow_handler(_):
        await asyncio.sleep(10)

    service = BailianGradingService(settings, transport=httpx.MockTransport(slow_handler))
    service.timeout = 0.01
    try:
        with pytest.raises(TimeoutError):
            await service.grade_short_answer(question, "answer")
    finally:
        await service.aclose()


async def test_factory_and_worker_resource_cleanup(settings, monkeypatch):
    import app.core.config as config
    import app.worker as entry

    monkeypatch.setattr(config, "get_settings", lambda: settings)
    service = get_grading_service()
    assert isinstance(service, BailianGradingService)
    await service.aclose()
    settings.ai_provider = "placeholder"
    assert isinstance(get_grading_service(), PlaceholderGradingService)
    settings.ai_provider = "unknown"
    with pytest.raises(ValueError, match="Unsupported"):
        get_grading_service()
    monkeypatch.setattr(entry, "get_settings", lambda: settings)
    monkeypatch.setattr(entry, "get_grading_service", lambda: service)
    monkeypatch.setattr(service, "aclose", AsyncMock())
    monkeypatch.setattr(entry, "close_database", AsyncMock())
    for init_failure in [False, True]:
        monkeypatch.setattr(entry, "init_database", AsyncMock(side_effect=RuntimeError("init") if init_failure else None))
        monkeypatch.setattr(entry.GradingWorker, "run", AsyncMock(side_effect=asyncio.CancelledError()))
        with pytest.raises(RuntimeError if init_failure else asyncio.CancelledError):
            await entry.main()
    assert service.aclose.await_count == 2 and entry.close_database.await_count == 2


async def test_adapter_errors_use_existing_worker_policy(settings, question):
    for status, final_status, code in [(401, "failed", "provider_authentication"),
                                      (429, "pending", "provider_unavailable")]:
        service = BailianGradingService(settings, transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="secret")))
        worker = GradingWorker(settings, service)
        async def grade(_):
            return await service.grade_short_answer(question, "answer")

        worker.grade = grade
        worker.finish = AsyncMock(return_value=True)
        try:
            await worker.execute({"ai_cycle_attempts": 1})
            changes = worker.finish.call_args.args[1]
            assert changes == {"ai_status": final_status, "ai_error_code": code}
        finally:
            await service.aclose()
