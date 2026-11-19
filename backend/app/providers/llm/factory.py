from app.core.config import Settings
from app.providers.llm.anthropic import AnthropicProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.fake import FakeLLMProvider
from app.providers.llm.openai import OpenAIProvider


def make_llm_provider(settings: Settings, *, answers: bool = False) -> LLMProvider:
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
    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise ValueError("MEETPILOT_ANTHROPIC_API_KEY is required for the anthropic provider")
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=(settings.anthropic_answer_model or settings.anthropic_model)
            if answers
            else settings.anthropic_model,
            base_url=settings.anthropic_base_url,
            timeout=settings.anthropic_timeout_seconds,
        )
    return FakeLLMProvider()
