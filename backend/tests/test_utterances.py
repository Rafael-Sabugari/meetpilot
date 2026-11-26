"""One question split by the speech recognizer at a pause still gives one answer."""

import asyncio
from collections import deque
from typing import Any

from app.ai.questions import AutoAnswerPolicy
from app.providers.llm.fake import FakeLLMProvider
from app.realtime.assist import LiveAssist
from app.realtime.events import EventType, TranscriptSegment


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def seg(i: int, text: str, source: str = "system") -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=f"{source}-{i}",
        source=source,
        text=text,
        start_ms=i * 1000,
        end_ms=i * 1000 + 900,
        is_final=True,
    )


def make(gap: float = 1.5) -> tuple[LiveAssist, list[tuple[str, dict[str, Any]]], Clock]:
    events: list[tuple[str, dict[str, Any]]] = []
    clock = Clock()

    async def emit(kind: EventType, payload: dict[str, Any]) -> None:
        events.append((str(kind.value), payload))

    a = LiveAssist(
        emit=emit,
        llm=FakeLLMProvider(),
        meeting_language="pt-BR",
        context=deque(maxlen=400),
        copilot_remaining=100,
        max_context_chars=12_000,
        max_output_tokens=500,
        policy=AutoAnswerPolicy(min_interval=0),
        clock=clock,
        utterance_gap=gap,
    )
    return a, events, clock


def detected(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [p for k, p in events if k == "question.detected"]


def test_split_question_is_judged_as_one_after_the_speaker_pauses() -> None:
    async def run() -> list[tuple[str, dict[str, Any]]]:
        a, events, clock = make()
        await a.on_final(seg(1, "Claro. Me conta caso em que você trabalhou com data cloud"))
        clock.t += 0.8  # short pause inside the sentence
        await a.tick()
        await a.on_final(seg(2, "ou marketing cloud, destacando sua participação no projeto."))
        clock.t += 0.5
        await a.tick()
        assert detected(events) == [], "still talking: nothing yet"
        clock.t += 1.2
        await a.tick()
        await a.drain(5)
        return events

    events = asyncio.run(run())
    found = detected(events)
    assert len(found) == 1
    q = found[0]
    assert q["text"] == (
        "Claro. Me conta caso em que você trabalhou com data cloud ou marketing cloud, "
        "destacando sua participação no projeto."
    )
    assert q["segment_ids"] == ["system-1", "system-2"]
    assert q["segment_id"] == "system-2"
    assert len([1 for k, _ in events if k == "answer.completed"]) == 1


def test_request_split_before_its_verb_is_still_recognized() -> None:
    async def run() -> list[tuple[str, dict[str, Any]]]:
        a, events, clock = make()
        await a.on_final(seg(1, "Ótimo. E me conta um pouco,"))
        clock.t += 0.4
        await a.on_final(seg(2, "sobre um projeto recente com data cloud."))
        clock.t += 2
        await a.tick()
        await a.drain(5)
        return events

    assert len(detected(asyncio.run(run()))) == 1


def test_user_speaking_ends_the_other_persons_turn_immediately() -> None:
    async def run() -> list[tuple[str, dict[str, Any]]]:
        a, events, _ = make()
        await a.on_final(seg(1, "Você pode explicar como funciona a integração?"))
        await a.on_final(seg(2, "Claro, então…", source="microphone"))
        found = detected(events)
        await a.drain(5)
        return found  # type: ignore[return-value]

    assert len(asyncio.run(run())) == 1


def test_stopping_the_meeting_does_not_lose_the_last_question() -> None:
    async def run() -> list[tuple[str, dict[str, Any]]]:
        a, events, _ = make()
        await a.on_final(seg(1, "Qual é a sua experiência com Kubernetes?"))
        await a.drain(5)
        return events

    assert len(detected(asyncio.run(run()))) == 1


def test_identical_translation_is_not_shown() -> None:
    from app.ai.translation import same_text

    assert same_text("Ótimo. E me conta pouco,", "otimo e me conta pouco")
    assert not same_text("Great, tell me more", "Ótimo, me conte mais")


def test_a_complete_question_ends_the_turn_without_waiting() -> None:
    from app.realtime.assist import ends_turn

    async def run() -> list[tuple[str, dict[str, Any]]]:
        a, events, _ = make()
        await a.on_final(seg(1, "E como você lida com governor limits em triggers?"))
        found = detected(events)  # no clock advance, no tick
        await a.drain(5)
        return found  # type: ignore[return-value]

    assert len(asyncio.run(run())) == 1
    assert ends_turn("Me conta, como foi a migração?")
    assert not ends_turn("Data cloud ou?"), "dangling: they are still talking"
    assert not ends_turn("Certo?"), "too short to be the whole question"
    assert not ends_turn("Você usou Bulk API e?")
    assert not ends_turn("Me conta como foi a migração.")
