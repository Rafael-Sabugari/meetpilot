import pytest

from app.realtime.noise import is_noise


@pytest.mark.parametrize(
    "text",
    [
        "s, the.s, the.s, the.s, the.s, the.s, the.s, the.s, the.s, the.s",
        "W-icкм. all gone Currys dob. sheesh serso. W-ic, do cleaned. keuf. zij.& luck. "
        "3.0. Schro. dinner. sserso. ogo. Tr rabbits. navigation. Ro.",
        "...",
        "♪ ♪",
        "3.0",
        "you you you you you you you",
        "I'm going to I'm going to I'm going to I'm going to",
    ],
)
def test_garbage_is_dropped(text: str) -> None:
    assert is_noise(text)


@pytest.mark.parametrize(
    "text",
    [
        "Okay. That was clear.",
        "We'll be right back.",
        "All right.",
        "Can you walk me through a production incident?",
        "In a Salesforce save cycle, what runs first, validation rules or record-triggered flows?",
        "Sim, sim. Pode falar.",
        "Então, o que você acha da arquitetura que a gente desenhou para a integração?",
        "No, no, no, that's not what I meant.",
        "Привет, как дела?",  # another language, but one alphabet per word: kept
        "Yes. Yes. Okay. Sure.",
    ],
)
def test_real_speech_is_kept(text: str) -> None:
    assert not is_noise(text)
