import json
from pathlib import Path

from evals.checks import Expect, language_of, run_checks, sentences
from evals.run import feedback_cases, load_cases


def failed(answer: str, **kw: object) -> set[str]:
    return {c.name for c in run_checks(answer, Expect(**kw)) if not c.ok}  # type: ignore[arg-type]


def test_checks_catch_rehearsed_and_written_style() -> None:
    good = "Eu bulkifico tudo. Uso um handler por objeto e mando o pesado pra Queueable."
    assert failed(good, must_any=["handler"]) == set()
    assert "sem elogio de abertura" in failed("Boa pergunta! Eu bulkifico tudo.")
    assert "sem oferta no final" in failed("Eu bulkifico tudo. Quer que eu detalhe?")
    assert "sem tópicos (falado)" in failed("Eu faço assim:\n- bulk\n- handler")
    assert "cita o essencial" in failed(good, must_any=["Platform Events"])
    assert "não inventa" in failed(good + " Foi na Heroku.", must_not=["Heroku"])
    assert "abertura variada" in failed("Concordo, a gente cria.", no_agree_opener=True)
    assert "idioma" in failed("I would use Platform Events for that.")
    assert "até 2 frases" in failed(
        "Uma frase aqui. Outra frase aqui. Mais uma aqui.", max_sentences=2
    )


def test_language_and_sentences() -> None:
    assert language_of("I would use the event bus for that") == "en"
    assert language_of("Eu acho que a gente devia usar isso") == "pt-BR"
    assert sentences("Uma frase. Outra frase! E mais uma?") == 3


def test_cases_file_and_feedback_import(tmp_path: Path) -> None:
    profile, cases = load_cases(Path("evals/cases.json"), None)
    assert "FICTÍCIA" in profile and len(cases) >= 10
    assert {c.scenario for c in cases} == {"interview", "meeting"}
    _, only = load_cases(Path("evals/cases.json"), {"int-salary"})
    assert [c.id for c in only] == ["int-salary"]
    fb = tmp_path / "fb.json"
    fb.write_text(
        json.dumps(
            [
                {"question": "Como você testa?", "answer": "Com 200 registros.", "rating": 1},
                {"question": "x", "answer": "y", "rating": -1},
            ]
        )
    )
    [case] = feedback_cases(fb)
    assert case.reference == "Com 200 registros." and case.id == "fb-1"
