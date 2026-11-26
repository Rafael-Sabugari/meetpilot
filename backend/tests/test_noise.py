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
        "J- Transd-als »-Pass-lo- Sch- Eventually-iyorsun-26-",
        "and iring.WG.yes of t shoulders.D. p, shoot! kry,y,",
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
        "We use Trigger.new and Account.Name in the handler, e.g. for the check-in flow.",
        "It's a well-known, high-volume, real-time integration.",
    ],
)
def test_real_speech_is_kept(text: str) -> None:
    assert not is_noise(text)


def test_clean_removes_garbage_inside_real_speech() -> None:
    from app.realtime.noise import clean

    assert (
        clean("Acho que a casinha é do… do… do… do… do Marcos") == "Acho que a casinha é do Marcos"
    )
    assert (
        clean("subiu o teste. 1.1.1.1.1.1. 1. 1. 1. 1. stakeholder") == "subiu o teste. stakeholder"
    )
    assert (
        clean("an, Has,AT,,, lee,, other,, 香港,,,, O Carlos também")
        == "an, Has,AT, lee, other, O Carlos também"
    )
    assert clean("ARQUITETURA, ARRADO, AQUI, ARQUI, ARQUI, ARQUI. Para o time") == (
        "ARQUITETURA, ARRADO, AQUI, ARQUI. Para o time"
    )
    assert clean("Normal. Normal. Normal. Eu tava olhando") == "Normal. Eu tava olhando"
    assert clean("Essa coluna do Sensoria, pra mim") == "Essa coluna do Sensoria, pra mim"
    assert (
        clean("são 105 histórias e 3 ambientes, dev e prod")
        == "são 105 histórias e 3 ambientes, dev e prod"
    )
    assert clean("posso botar 4, 5, 6, 6, hanno s Rein") == "posso botar hanno s Rein"
    assert clean("�� 아주 香港") == ""
