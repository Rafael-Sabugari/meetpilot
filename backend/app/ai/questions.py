"""Lightweight question detection (brief §7.3) — no LLM call needed to decide.

Stages:
1. Only *final* segments from other participants (`system` audio) are considered.
2. Interrogative intent: "?" (STT punctuation), interrogative openers, or request openers
   ("me explica…", "walk me through…").
3. Directed at the user: second-person cues ("você", "your", …) or a request.
4. Tag/rhetorical questions ("né?", "certo?", "right?") and very short fragments are ignored.
5. `AutoAnswerPolicy` debounces, de-duplicates and rate-limits before any LLM call.
"""

import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

AutoMode = Literal["off", "directed", "all"]


def _norm(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


REQUEST_OPENERS = (
    # pt
    "me fala",
    "me fale",
    "me conta",
    "me conte",
    "me explica",
    "me explique",
    "explica",
    "explique",
    "conta pra gente",
    "fala um pouco",
    "fale um pouco",
    "descreve",
    "descreva",
    "comenta",
    "comente",
    # en
    "tell me",
    "tell us",
    "walk me through",
    "walk us through",
    "explain",
    "describe",
    "talk about",
    "share",
)
SECOND_PERSON = {
    # pt
    "voce",
    "voces",
    "tu",
    "te",
    "teu",
    "tua",
    "seu",
    "sua",
    "seus",
    "suas",
    "contigo",
    "vc",
    "vcs",
    # en
    "you",
    "your",
    "yours",
    "yourself",
}
SECOND_PERSON_PHRASES = ("o que acha", "o que voce acha", "sua opiniao", "what do you think")
TAG_QUESTIONS = {
    "ne",
    "certo",
    "ok",
    "ta",
    "entendeu",
    "entenderam",
    "beleza",
    "sim",
    "nao",
    "tudo bem",
    "faz sentido",
    "pode ser",
    "combinado",
    "alo",
    "oi",
    "right",
    "okay",
    "you know",
    "isn t it",
    "correct",
    "makes sense",
    "yeah",
    "hello",
    "can you hear me",
    "da pra me ouvir",
    "voces estao me ouvindo",
    "estao me ouvindo",
    "me ouvem",
}
MIN_WORDS = 3


@dataclass(frozen=True)
class Assessment:
    is_question: bool
    directed: bool
    reason: str


# Words people put before a question or request ("Claro. E me conta…", "Ok, so tell me…").
FILLERS = frozenset(
    [
        "e",
        "entao",
        "claro",
        "otimo",
        "ok",
        "okay",
        "beleza",
        "certo",
        "perfeito",
        "legal",
        "bom",
        "bem",
        "show",
        "tranquilo",
        "and",
        "so",
        "ok",
        "great",
        "cool",
        "right",
        "alright",
        "now",
        "well",
        "perfect",
    ]
)
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")


def _strip_fillers(n: str) -> str:
    words = n.split()
    while words and words[0] in FILLERS:
        words.pop(0)
    return " ".join(words)


def _is_request(n: str) -> bool:
    return any(n == r or n.startswith(r + " ") for r in REQUEST_OPENERS)


def assess(text: str) -> Assessment:
    n = _norm(text)
    words = n.split()
    if not words:
        return Assessment(False, False, "empty")
    if n in TAG_QUESTIONS or (text.rstrip().endswith("?") and len(words) < MIN_WORDS):
        return Assessment(False, False, "tag_or_too_short")
    # A merged utterance can hold several sentences: judge each one.
    sentences = [x for x in _SENTENCE.split(text.strip()) if x.strip()]
    asks = False
    request = False
    for sent in sentences:
        sn = _norm(sent)
        if sent.rstrip().endswith("?") and sn not in TAG_QUESTIONS and len(sn.split()) >= MIN_WORDS:
            asks = True
        if _is_request(_strip_fillers(sn)):
            request = True
    # Openers alone ("Como eu disse…") are statements too often; we rely on the STT's "?"
    # for questions and on explicit request phrases for imperatives.
    if not (asks or request):
        return Assessment(False, False, "not_a_question")
    second = bool(SECOND_PERSON.intersection(words)) or any(p in n for p in SECOND_PERSON_PHRASES)
    if len(words) < MIN_WORDS:
        return Assessment(False, False, "too_short")
    return Assessment(True, second or request, "request" if request else "question")


INTERROGATIVE_CUES = (
    # pt (accents stripped)
    "como",
    "qual",
    "quais",
    "quando",
    "onde",
    "por que",
    "porque",
    "pq",
    "quem",
    "quanto",
    "quantos",
    "quantas",
    "o que",
    "sera que",
    "pode",
    "poderia",
    "consegue",
    "conseguiria",
    "ja trabalhou",
    "ja fez",
    "tem experiencia",
    "e o",
    "e a",
    "e os",
    "e as",
    # en
    "what",
    "how",
    "why",
    "when",
    "where",
    "who",
    "which",
    "do you",
    "did you",
    "have you",
    "are you",
    "were you",
    "can you",
    "could you",
    "would you",
    "will you",
    "is there",
    "is it",
    "any thoughts",
)


def worth_checking(text: str, a: Assessment, mode: AutoMode) -> bool:
    """Lines the rules could not settle but that may still be a question for the user.

    STT often drops the "?" and people ask without "você"/"you" ("E o prazo, como fica").
    Such lines get a cheap AI check (`app/ai/question_check.py`) with recent context.
    """
    if mode == "off" or a.reason in ("empty", "tag_or_too_short", "too_short"):
        return False
    if a.is_question:
        # Confirmed question; only its target is unclear.
        return mode == "directed" and not a.directed
    n = _norm(text)
    words = n.split()
    if len(words) < MIN_WORDS:
        return False
    padded = f" {n} "
    cue = any(f" {c} " in padded for c in INTERROGATIVE_CUES)
    second = bool(SECOND_PERSON.intersection(words))
    return cue or second


def should_answer(a: Assessment, mode: AutoMode) -> bool:
    if mode == "off" or not a.is_question:
        return False
    return mode == "all" or a.directed


@dataclass
class AutoAnswerPolicy:
    """Decides when a detected question gets its automatic answer.

    * at most one answer in flight; questions asked meanwhile wait in a short FIFO queue
      (answered in the order they were asked; the oldest is dropped if the queue is full)
    * `min_interval` between answers; `max_per_minute` cap
    * near-duplicate questions (same normalized text) within `dedupe_window` are skipped
    * a waiting question older than `stale_after` seconds is dropped (the moment has passed)
    """

    min_interval: float = 3.0
    max_per_minute: int = 10
    dedupe_window: float = 60.0
    stale_after: float = 20.0
    max_waiting: int = 3
    _last_started: float = -1e9
    _recent_starts: deque[float] = field(default_factory=deque)
    _seen: deque[tuple[float, str]] = field(default_factory=deque)
    _in_flight: bool = False
    _waiting: deque[tuple[float, str]] = field(default_factory=deque)

    def _prune(self, now: float) -> None:
        while self._recent_starts and now - self._recent_starts[0] > 60:
            self._recent_starts.popleft()
        while self._seen and now - self._seen[0][0] > self.dedupe_window:
            self._seen.popleft()
        while self._waiting and now - self._waiting[0][0] > self.stale_after:
            self._waiting.popleft()

    def _is_duplicate(self, key: str) -> bool:
        return any(k == key for _, k in self._seen) or any(
            _norm(t) == key for _, t in self._waiting
        )

    def _allowed(self, now: float) -> bool:
        return (
            now - self._last_started >= self.min_interval
            and len(self._recent_starts) < self.max_per_minute
        )

    def offer(self, text: str, now: float) -> bool:
        """A question was detected. Returns True if it should be answered now."""
        self._prune(now)
        key = _norm(text)
        if self._is_duplicate(key):
            return False
        if self._in_flight or self._waiting or not self._allowed(now):
            self._waiting.append((now, text))
            while len(self._waiting) > self.max_waiting:
                self._waiting.popleft()
            return False
        self._start(now, key)
        return True

    def _start(self, now: float, key: str) -> None:
        self._in_flight = True
        self._last_started = now
        self._recent_starts.append(now)
        self._seen.append((now, key))

    def finished(self, now: float) -> str | None:
        """The in-flight answer ended. Returns the next waiting question to answer, if any."""
        self._in_flight = False
        return self._next(now)

    def _next(self, now: float) -> str | None:
        self._prune(now)
        if not self._waiting or not self._allowed(now):
            return None  # still rate-limited: tick() retries
        _, text = self._waiting.popleft()
        self._start(now, _norm(text))
        return text

    def tick(self, now: float) -> str | None:
        """Periodic check: start a waiting question once limits allow."""
        if self._in_flight:
            return None
        return self._next(now)
