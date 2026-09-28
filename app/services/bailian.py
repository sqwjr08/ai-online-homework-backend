import asyncio
import json
import time
from datetime import UTC, datetime

import httpx

from app.core.config import Settings
from app.models import QuestionSnapshot
from app.services.grading import (
    GradeResult, GradingService, PermanentGradingError, TransientGradingError,
)
from app.services.grading_contract import build_grading_messages, parse_grading_result, unique_object
from app.services.grading_trace import emit_call, read_usage, safe_request_id

MAX_ENVELOPE_BYTES = 64 * 1024
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class BailianGradingService(GradingService):
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        if settings.bailian_api_key is None:
            raise ValueError("BAILIAN_API_KEY is required")
        self.model = settings.bailian_model
        self.timeout = settings.bailian_request_timeout_seconds
        self.max_tokens = settings.bailian_max_output_tokens
        self.client = httpx.AsyncClient(
            base_url=settings.bailian_base_url + "/",
            headers={"Authorization": f"Bearer {settings.bailian_api_key.get_secret_value()}"},
            timeout=httpx.Timeout(self.timeout), follow_redirects=False, trust_env=False,
            transport=transport if transport is not None else httpx.AsyncHTTPTransport(retries=0),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def grade_short_answer(self, question: QuestionSnapshot, answer_text: str) -> GradeResult:
        started = time.monotonic()
        event = {"event": "ai_call", "started_at": datetime.now(UTC).isoformat(),
                 "provider": "bailian", "model": self.model, "request_id": None,
                 "input_tokens": None, "output_tokens": None, "http_status": None,
                 "status": "failed", "error_code": "unexpected_error"}
        try:
            result = await self._grade(question, answer_text, event)
            event.update(status="succeeded", error_code=None)
            return result
        except asyncio.CancelledError:
            event.update(status="cancelled", error_code="cancelled")
            raise
        except TimeoutError:
            event["error_code"] = "timeout"
            raise
        except PermanentGradingError as exc:
            event["error_code"] = exc.code
            raise
        except TransientGradingError:
            event["error_code"] = "provider_unavailable"
            raise
        finally:
            event["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            emit_call(event)

    async def _grade(self, question, answer_text, event):
        payload = {
            "model": self.model, "messages": build_grading_messages(question, answer_text),
            "stream": False, "enable_thinking": False,
            "response_format": {"type": "json_object"}, "max_tokens": self.max_tokens,
        }
        try:
            # Wall-clock timeout also bounds a peer that continually trickles response bytes.
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("POST", "chat/completions", json=payload) as response:
                    event["http_status"] = response.status_code
                    event["request_id"] = safe_request_id(response.headers.get("x-request-id"))
                    if response.status_code in TRANSIENT_STATUS_CODES:
                        raise TransientGradingError("Bailian temporarily unavailable")
                    if response.status_code in {401, 403}:
                        raise PermanentGradingError("provider_authentication")
                    if response.status_code != 200:
                        raise PermanentGradingError("provider_request_rejected")
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        body.extend(chunk)
                        if len(body) > MAX_ENVELOPE_BYTES:
                            raise PermanentGradingError("invalid_provider_response")
        except httpx.TimeoutException:
            raise TimeoutError("Bailian request timed out") from None
        except TimeoutError:
            raise TimeoutError("Bailian request timed out") from None
        except httpx.TransportError:
            raise TransientGradingError("Bailian transport unavailable") from None

        try:
            envelope = json.loads(body, object_pairs_hook=unique_object)
            if not isinstance(envelope, dict) or "error" in envelope:
                raise ValueError("Invalid envelope")
            event["request_id"] = event["request_id"] or safe_request_id(envelope.get("request_id"))
            read_usage(envelope, event)
            choices = envelope["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("Invalid choices")
            choice = choices[0]
            message = choice["message"]
            if (choice["finish_reason"] != "stop" or message.get("role") != "assistant"
                    or message.get("refusal") or message.get("tool_calls") or message.get("function_call")):
                raise ValueError("Incomplete or refused response")
            return parse_grading_result(message["content"], question.max_score)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise PermanentGradingError("invalid_provider_response") from None
