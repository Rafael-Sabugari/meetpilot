from app.core.config import Settings
from app.providers.stt.base import StreamingSTTProvider
from app.providers.stt.deepgram import DeepgramProvider
from app.providers.stt.fake import FakeSTTProvider


def make_stt_provider(settings: Settings) -> StreamingSTTProvider:
    if settings.stt_provider == "deepgram":
        if settings.deepgram_api_key is None:
            raise ValueError("MEETPILOT_DEEPGRAM_API_KEY is required for the deepgram provider")
        return DeepgramProvider(
            api_key=settings.deepgram_api_key.get_secret_value(),
            url=settings.deepgram_url,
            model=settings.deepgram_model,
            mip_opt_out=settings.deepgram_mip_opt_out,
        )
    return FakeSTTProvider()
