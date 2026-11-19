"""Runtime configuration. Secrets come only from the environment / secret manager."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEETPILOT_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "staging", "production"] = "development"
    version: str = "0.1.0"
    log_level: str = "INFO"

    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)

    # Signing key for access tokens. Required outside development/test; in development a
    # random per-process key is generated (tokens stop working after a restart).
    jwt_secret: SecretStr | None = None
    access_token_ttl_minutes: int = 15
    stream_token_ttl_seconds: int = 120
    refresh_token_ttl_days: int = 30

    # Plans / entitlements (minutes of processed audio per calendar month).
    trial_minutes_per_month: int = 60

    # Speech-to-text. "fake" is a deterministic local provider for development and tests.
    stt_provider: Literal["fake", "deepgram"] = "fake"
    deepgram_api_key: SecretStr | None = None
    deepgram_url: str = "wss://api.deepgram.com/v1/listen"
    deepgram_model: str = "nova-3"
    # Exclude our requests from Deepgram's Model Improvement Program (training).
    deepgram_mip_opt_out: bool = True

    # Copilot (LLM). "fake" is a deterministic local provider for development and tests.
    llm_provider: Literal["fake", "openai", "anthropic"] = "fake"
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    # Cheapest current Claude model (US$0.10 / 0.50 per M tokens, Nov 2026).
    anthropic_model: str = "claude-haiku-5-5"
    # Optional stronger model just for suggested answers (question checks and live
    # translation stay on anthropic_model). Empty = same as anthropic_model.
    anthropic_answer_model: str = ""
    anthropic_timeout_seconds: float = 30.0
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"
    openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    openai_timeout_seconds: float = 30.0
    copilot_requests_per_month: int = 200
    copilot_requests_per_minute: int = 20
    # Whole conversation for most meetings (~1 h); the oldest lines drop first beyond it.
    copilot_max_context_chars: int = 60_000
    # Room for a long spoken answer plus its translation.
    copilot_max_output_tokens: int = 2_000
    # Automatic suggestions: minimum seconds between two auto answers and cap per minute.
    copilot_auto_min_interval_seconds: float = 3.0
    copilot_auto_max_per_minute: int = 10
    # Silence (s) from the other side that ends an utterance before question detection;
    # 0 judges every recognized segment on its own.
    copilot_utterance_gap_seconds: float = 1.5

    # Privacy defaults (docs/SECURITY_PRIVACY.md): raw audio is never stored by default.
    store_raw_audio_default: bool = False
    transcript_retention_days_default: int = 30

    # Realtime audio limits.
    max_audio_frame_bytes: int = 64_000
    max_concurrent_sessions_per_user: int = 1

    @model_validator(mode="after")
    def _require_secrets_outside_dev(self) -> "Settings":
        if self.env in ("staging", "production"):
            missing = [
                name
                for name, value in (
                    ("jwt_secret", self.jwt_secret),
                    ("database_url", self.database_url),
                )
                if value is None or not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"missing required settings for {self.env}: {', '.join(missing)}")
            if self.stt_provider == "fake":
                raise ValueError("stt_provider=fake is not allowed in staging/production")
            if self.stt_provider == "deepgram" and not self.deepgram_api_key:
                raise ValueError("deepgram_api_key is required when stt_provider=deepgram")
            if self.llm_provider == "fake":
                raise ValueError("llm_provider=fake is not allowed in staging/production")
            if self.llm_provider == "openai" and not self.openai_api_key:
                raise ValueError("openai_api_key is required when llm_provider=openai")
            if self.llm_provider == "anthropic" and not self.anthropic_api_key:
                raise ValueError("anthropic_api_key is required when llm_provider=anthropic")
            if self.store_raw_audio_default:
                raise ValueError(
                    "store_raw_audio_default must be False; audio storage is opt-in per user"
                )
        return self

    def effective_database_url(self) -> str:
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        if self.env == "test":
            return "sqlite+aiosqlite://"
        return "sqlite+aiosqlite:///./meetpilot-dev.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
