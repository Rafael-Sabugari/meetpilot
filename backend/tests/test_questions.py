import pytest

from app.ai.questions import AutoAnswerPolicy, assess, should_answer


@pytest.mark.parametrize(
    "text",
    [
        "Você pode explicar como funciona a integração com o banco?",
        "Qual é a sua experiência com Kubernetes?",
        "Rafael, o que você acha dessa proposta?",
        "Me fala um pouco sobre o seu último projeto.",
        "Me explica como vocês fazem o deploy",
        "Can you walk us through the architecture?",
        "What do you think about the timeline?",
        "Tell me about your role in the migration.",
        "Vocês conseguem entregar até sexta?",
    ],
)
def test_questions_directed_at_the_user(text: str) -> None:
    a = assess(text)
    assert a.is_question and a.directed, a
    assert should_answer(a, "directed")


@pytest.mark.parametrize(
    "text",
    [
        "Qual é o prazo da entrega do módulo de pagamentos?",
        "Alguém sabe quando o servidor volta?",
        "What is the current error rate in production?",
    ],
)
def test_general_questions_only_in_all_mode(text: str) -> None:
    a = assess(text)
    assert a.is_question and not a.directed, a
    assert not should_answer(a, "directed")
    assert should_answer(a, "all")


@pytest.mark.parametrize(
    "text",
    [
        "Né?",
        "Certo?",
        "Tudo bem?",
        "Right?",
        "Vocês estão me ouvindo?",
        "Can you hear me?",
        "Ok?",
        "Bom dia a todos, vamos começar.",
        "Como eu disse antes, o prazo é sexta.",
        "Then we deploy on Friday.",
        "",
    ],
)
def test_not_questions_or_rhetorical(text: str) -> None:
    a = assess(text)
    assert not should_answer(a, "all"), (text, a)


def test_off_mode_never_answers() -> None:
    assert not should_answer(assess("Você pode explicar o plano?"), "off")


def test_accents_and_case_do_not_matter() -> None:
    assert assess("VOCÊ PODE EXPLICAR O PLANO?").directed
    assert assess("voce pode explicar o plano?").directed


class TestPolicy:
    def test_first_question_answers_immediately(self) -> None:
        p = AutoAnswerPolicy()
        assert p.offer("Você pode explicar?", now=0)

    def test_questions_asked_while_busy_are_all_answered_in_order(self) -> None:
        p = AutoAnswerPolicy(min_interval=0)
        assert p.offer("Pergunta A sobre o plano?", now=0)
        assert not p.offer("Pergunta B sobre o prazo?", now=1)
        assert not p.offer("Pergunta C sobre o custo?", now=2)
        assert p.finished(now=3) == "Pergunta B sobre o prazo?"
        assert p.finished(now=4) == "Pergunta C sobre o custo?"
        assert p.finished(now=5) is None

    def test_waiting_queue_is_short_and_drops_the_oldest(self) -> None:
        p = AutoAnswerPolicy(min_interval=0, max_waiting=2)
        assert p.offer("Pergunta A sobre o plano?", now=0)
        for i, q in enumerate(["B sobre prazo?", "C sobre custo?", "D sobre equipe?"]):
            p.offer(f"Pergunta {q}", now=1 + i)
        assert p.finished(now=5) == "Pergunta C sobre custo?"
        assert p.finished(now=6) == "Pergunta D sobre equipe?"

    def test_a_question_waiting_in_line_is_not_queued_twice(self) -> None:
        p = AutoAnswerPolicy(min_interval=0)
        assert p.offer("Pergunta A sobre o plano?", now=0)
        assert not p.offer("Pergunta B sobre o prazo?", now=1)
        assert not p.offer("pergunta b sobre o prazo", now=2)
        assert p.finished(now=3) == "Pergunta B sobre o prazo?"
        assert p.finished(now=4) is None

    def test_duplicates_are_skipped(self) -> None:
        p = AutoAnswerPolicy(min_interval=0)
        assert p.offer("Você pode explicar o plano?", now=0)
        p.finished(now=1)
        assert not p.offer("você pode explicar o plano", now=2)
        assert p.offer("Você pode explicar o plano?", now=70), "after the dedupe window"

    def test_min_interval_and_tick(self) -> None:
        p = AutoAnswerPolicy(min_interval=6)
        assert p.offer("Primeira pergunta para você?", now=0)
        assert p.finished(now=1) is None
        assert not p.offer("Segunda pergunta para você?", now=2), "too soon"
        assert p.tick(now=4) is None
        assert p.tick(now=7) == "Segunda pergunta para você?"

    def test_stale_waiting_question_is_dropped(self) -> None:
        p = AutoAnswerPolicy(min_interval=0, stale_after=20)
        assert p.offer("Pergunta longa um?", now=0)
        assert not p.offer("Pergunta antiga dois?", now=1)
        assert p.finished(now=30) is None

    def test_per_minute_cap(self) -> None:
        p = AutoAnswerPolicy(min_interval=0, max_per_minute=3)
        started = 0
        for i in range(10):
            if p.offer(f"Pergunta número {i} para você?", now=i):
                started += 1
                p.finished(now=i + 0.5)
        assert started == 3


# -- lines worth an AI check -----------------------------------------------------------

from app.ai.context import ContextSegment  # noqa: E402
from app.ai.question_check import build_input as check_input  # noqa: E402
from app.ai.questions import worth_checking  # noqa: E402


@pytest.mark.parametrize(
    "text",
    [
        "E o prazo da integração, como fica",  # no "?" from the STT
        "Rafael quanto tempo você trabalhou com isso",
        "how did you handle the migration",
        "Qual é o prazo para a entrega do módulo?",  # question, target unclear
    ],
)
def test_unsettled_lines_are_worth_an_ai_check(text: str) -> None:
    assert worth_checking(text, assess(text), "directed")


@pytest.mark.parametrize(
    "text",
    [
        "Bom dia a todos, vamos começar a reunião.",
        "Precisamos fechar os testes até sexta-feira.",
        "Certo?",
        "ok",
        "Você pode explicar como funciona a integração?",  # rules already settled it
    ],
)
def test_clear_lines_are_not_checked(text: str) -> None:
    assert not worth_checking(text, assess(text), "directed")


def test_nothing_is_checked_when_suggestions_are_off() -> None:
    t = "E o prazo, como fica"
    assert not worth_checking(t, assess(t), "off")
    q = "Qual é o prazo para a entrega do módulo?"
    assert not worth_checking(q, assess(q), "all"), "all mode answers it without a check"


def test_check_input_has_recent_lines_and_short_user_context() -> None:
    segs = [
        ContextSegment(source="system" if i % 2 else "microphone", text=f"linha {i}", start_ms=i)
        for i in range(20)
    ]
    text = check_input(segs, "Sou o Rafael. " + "x" * 2000)
    body = text.split("<transcript>\n")[1]
    assert body.splitlines()[0] == "User: linha 12" and "Others: linha 19" in body
    assert body.count("\n") == 8  # exactly the last 8 lines
    assert "Sou o Rafael." in text and len(text.split("</user_context>")[0]) < 700
