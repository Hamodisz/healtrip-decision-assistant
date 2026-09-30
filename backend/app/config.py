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
    llm_disable_thinking: bool = False  # DeepSeek/Kimi reason by default and can return empty content

    rate_limit_per_minute: int = 20
    daily_turn_cap: int = 300
    seed_token: str = ""  # enables POST /api/v1/admin/seed only while set  # public-demo spend guard (~$0.00075/turn measured on deepseek-flash)

    @property
    def sqlalchemy_url(self) -> str:
        """Hosted Postgres (Neon etc.) gives postgres:// or postgresql:// URLs; we use the psycopg 3 driver."""
        u = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if u.startswith(prefix):
                return "postgresql+psycopg://" + u[len(prefix):]
        return u

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
