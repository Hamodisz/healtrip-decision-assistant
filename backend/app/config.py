from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment. No secrets in code."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://healtrip:healtrip@localhost:5433/healtrip"
    cors_origins: str = "http://localhost:3000"  # comma-separated; never "*"

    # Any OpenAI-compatible endpoint (Anthropic, Groq, Gemini, OpenAI). Key stays server-side.
    llm_base_url: str = "https://api.anthropic.com/v1"
    llm_model: str = "claude-haiku-4-5"
    llm_api_key: str = ""
    llm_timeout_s: float = 20.0

    rate_limit_per_minute: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
