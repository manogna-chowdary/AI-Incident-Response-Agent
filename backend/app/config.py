from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _blank_to_none(value: object) -> object:
    """Treat an empty or whitespace-only environment variable as unset.

    Without this, a line like HINDSIGHT_API_KEY= in .env becomes an empty
    SecretStr rather than None, and an "is not configured" check silently passes.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _blank_to(default: str):
    """Validator factory that falls back to a default when a value is blank."""

    def validator(value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return default
        return value

    return validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "AI Incident Response Agent API"
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/incident_response"
    )

    hindsight_api_url: str = "https://api.hindsight.vectorize.io"
    # SecretStr keeps the key masked in repr(), logs, and tracebacks. Call
    # get_secret_value() only at the point of use.
    hindsight_api_key: SecretStr | None = None
    hindsight_bank_id: str | None = None

    groq_api_key: SecretStr | None = None
    groq_model: str = DEFAULT_GROQ_MODEL
    groq_timeout_seconds: float = 30.0

    _normalise_hindsight = field_validator("hindsight_api_key", "hindsight_bank_id", mode="before")(
        _blank_to_none
    )
    _normalise_groq_key = field_validator("groq_api_key", mode="before")(_blank_to_none)
    _default_groq_model = field_validator("groq_model", mode="before")(
        _blank_to(DEFAULT_GROQ_MODEL)
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
