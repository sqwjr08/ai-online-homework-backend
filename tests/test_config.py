from pathlib import Path

from app.core.config import Settings


def test_example_env_loads(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)

    example_env = Path(__file__).resolve().parents[1] / ".env.example"
    settings = Settings(_env_file=example_env)

    assert settings.cors_origins == ["http://localhost:5173", "https://localhost:5173"]
    assert settings.environment == "development"
    assert settings.database_name == "answer_platform_dev"
