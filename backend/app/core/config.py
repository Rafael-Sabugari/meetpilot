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

    # Signing key for access tokens (auth slice). Required outside development/test.
    jwt_secret: SecretStr | None = None

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
            if self.store_raw_audio_default:
                raise ValueError(
                    "store_raw_audio_default must be False; audio storage is opt-in per user"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
