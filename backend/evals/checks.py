"""Deterministic checks for one suggestion (no model needed; unit-tested)."""

import re
from dataclasses import dataclass, field

from app.brain.search import fold

PRAISE = re.compile(
    r"^\s*(boa pergunta|ótima pergunta|otima pergunta|excelente pergunta|great question|"
    r"good question|que bom que perguntou)",
    re.IGNORECASE,
)
AGREE_OPENER = re.compile(r"^\s*(concordo|faz sentido|exato|boa[,.!]|agree|makes sense)", re.I)
CLOSER = re.compile(
    r"(quer que eu (detalhe|aprofunde|explique)|posso detalhar|want me to|"
    r"se quiser,? posso)[^.?!]*[.?!]?\s*$",
    re.IGNORECASE,
)
BULLETS = re.compile(r"^\s*([-*•]|\d+[.)])\s", re.MULTILINE)
_PT = {"que", "não", "nao", "você", "voce", "uma", "para", "com", "isso", "gente", "mais", "eu"}
_EN = {"the", "and", "you", "that", "with", "this", "for", "would", "what", "it's", "i"}


def language_of(text: str) -> str:
    words = re.findall(r"[a-záàâãéêíóôõúç']+", text.lower())
    pt = sum(w in _PT for w in words)
    en = sum(w in _EN for w in words)
    return "pt-BR" if pt >= en else "en"


def sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s.split()) >= 2])


@dataclass
class Expect:
    language: str = "pt-BR"
    max_sentences: int = 8
    must_any: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    no_agree_opener: bool = False


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def run_checks(answer: str, expect: Expect) -> list[CheckResult]:
    a = answer.strip()
    folded = fold(a)
    out = [
        CheckResult("idioma", language_of(a) == expect.language, language_of(a)),
        CheckResult("sem elogio de abertura", not PRAISE.search(a)),
        CheckResult("sem oferta no final", not CLOSER.search(a)),
        CheckResult("sem tópicos (falado)", not BULLETS.search(a)),
        CheckResult(
            f"até {expect.max_sentences} frases",
            sentences(a) <= expect.max_sentences,
            str(sentences(a)),
        ),
    ]
    if expect.must_any:
        hit = [t for t in expect.must_any if fold(t) in folded]
        out.append(CheckResult("cita o essencial", bool(hit), ", ".join(hit) or "nenhum"))
    if expect.must_not:
        bad = [t for t in expect.must_not if fold(t) in folded]
        out.append(CheckResult("não inventa", not bad, ", ".join(bad)))
    if expect.no_agree_opener:
        out.append(CheckResult("abertura variada", not AGREE_OPENER.search(a)))
    return out
