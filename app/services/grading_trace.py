"""Allowlisted call metadata only; never log provider bodies or exception text."""

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("app.ai_calls")
call_context = ContextVar("grading_call_context", default=None)


@contextmanager
def grading_context(submission_id, question_id, attempt):
    token = call_context.set({"submission_id": str(submission_id),
                              "question_id": str(question_id), "attempt": attempt})
    try:
        yield
    finally:
        call_context.reset(token)


def safe_request_id(value):
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        return value
    return None


def read_usage(envelope, event):
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return
    for source, target in [("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")]:
        value = usage.get(source)
        if type(value) is int and 0 <= value <= 10**12:
            event[target] = value


def emit_call(event):
    try:
        logger.info(json.dumps({**(call_context.get() or {}), **event}, ensure_ascii=True))
    except Exception:
        # A failed local log write must not turn a successful model call into a retry.
        pass


class QuietRotatingHandler(RotatingFileHandler):
    def handleError(self, record):
        pass


def configure_call_logging(directory=Path(".logs")):
    directory.mkdir(parents=True, exist_ok=True)
    handler = QuietRotatingHandler(directory / "ai-calls.jsonl", maxBytes=5 * 1024 * 1024,
                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    return handler
