from app.core.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import FakeLLMProvider
from app.providers.llm.openai import OpenAIProvider


def make_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ValueError("MEETPILOT_OPENAI_API_KEY is required for the openai provider")
        return OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            reasoning_effort=settings.openai_reasoning_effort,
            timeout=settings.openai_timeout_seconds,
        )
    return FakeLLMProvider()
