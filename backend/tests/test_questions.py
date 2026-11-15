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

    def test_coalesces_while_busy_and_runs_newest_next(self) -> None:
        p = AutoAnswerPolicy(min_interval=0)
        assert p.offer("Pergunta A sobre o plano?", now=0)
        assert not p.offer("Pergunta B sobre o prazo?", now=1)
        assert not p.offer("Pergunta C sobre o custo?", now=2)
        assert p.finished(now=3) == "Pergunta C sobre o custo?"
        assert p.finished(now=5) is None

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
