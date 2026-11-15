import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_development_defaults_are_private() -> None:
    s = Settings(env="development")
    assert s.store_raw_audio_default is False
    assert s.max_concurrent_sessions_per_user == 1


def test_production_requires_secrets() -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(env="production")


def test_production_rejects_default_audio_storage() -> None:
    with pytest.raises(ValidationError, match="opt-in"):
        Settings(
            env="production",
            jwt_secret="x" * 32,
            database_url="postgresql://db/x",
            stt_provider="deepgram",
            deepgram_api_key="dg",
            store_raw_audio_default=True,
        )


def test_secrets_are_not_rendered() -> None:
    s = Settings(env="development", jwt_secret="super-secret-value")
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in s.model_dump_json()


def test_production_rejects_fake_stt() -> None:
    with pytest.raises(ValidationError, match="fake"):
        Settings(env="production", jwt_secret="x" * 32, database_url="postgresql://db/x")


def test_deepgram_requires_key_in_production() -> None:
    with pytest.raises(ValidationError, match="deepgram_api_key"):
        Settings(
            env="production",
            jwt_secret="x" * 32,
            database_url="postgresql://db/x",
            stt_provider="deepgram",
        )


def test_deepgram_training_opt_out_is_default() -> None:
    assert Settings(env="development").deepgram_mip_opt_out is True
