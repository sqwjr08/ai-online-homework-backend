import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_worker_timing_constraints():
    settings = Settings(_env_file=None)
    assert settings.ai_lease_seconds > settings.ai_job_timeout_seconds > 0
    for values in [{"ai_job_timeout_seconds": 90}, {"ai_poll_seconds": 0},
                   {"ai_lease_seconds": 0}, {"ai_job_timeout_seconds": -1},
                   {"ai_max_attempts": 0}, {"ai_max_attempts": 11}, {"ai_retry_base_seconds": 0}]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **values)


def test_retry_request_requires_explicit_version():
    from app.schemas import RetryGradingRequest

    assert RetryGradingRequest(expected_retry_count=0).expected_retry_count == 0
    for values in [{}, {"expected_retry_count": -1}, {"expected_retry_count": True},
                   {"expected_retry_count": "0"}, {"expected_retry_count": 2**63},
                   {"expected_retry_count": 0, "force": True}]:
        with pytest.raises(ValidationError):
            RetryGradingRequest(**values)
