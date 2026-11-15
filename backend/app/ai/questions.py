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


def assess(text: str) -> Assessment:
    n = _norm(text)
    words = n.split()
    if not words:
        return Assessment(False, False, "empty")
    if n in TAG_QUESTIONS or (text.rstrip().endswith("?") and len(words) < MIN_WORDS):
        return Assessment(False, False, "tag_or_too_short")
    asks = text.rstrip().endswith("?")
    request = any(n == r or n.startswith(r + " ") for r in REQUEST_OPENERS)
    # Openers alone ("Como eu disse…") are statements too often; we rely on the STT's "?"
    # for questions and on explicit request phrases for imperatives.
    if not (asks or request):
        return Assessment(False, False, "not_a_question")
    second = bool(SECOND_PERSON.intersection(words)) or any(p in n for p in SECOND_PERSON_PHRASES)
    if len(words) < MIN_WORDS:
        return Assessment(False, False, "too_short")
    return Assessment(True, second or request, "request" if request else "question")


def should_answer(a: Assessment, mode: AutoMode) -> bool:
    if mode == "off" or not a.is_question:
        return False
    return mode == "all" or a.directed


@dataclass
class AutoAnswerPolicy:
    """Decides whether a detected question triggers an LLM call now.

    * at most one answer in flight; while busy, only the newest question waits (coalescing)
    * `min_interval` between answers; `max_per_minute` cap
    * near-duplicate questions (same normalized text) within `dedupe_window` are skipped
    """

    min_interval: float = 6.0
    max_per_minute: int = 6
    dedupe_window: float = 60.0
    stale_after: float = 20.0
    _last_started: float = -1e9
    _recent_starts: deque[float] = field(default_factory=deque)
    _seen: deque[tuple[float, str]] = field(default_factory=deque)
    _in_flight: bool = False
    _waiting: tuple[float, str] | None = None

    def _prune(self, now: float) -> None:
        while self._recent_starts and now - self._recent_starts[0] > 60:
            self._recent_starts.popleft()
        while self._seen and now - self._seen[0][0] > self.dedupe_window:
            self._seen.popleft()

    def _is_duplicate(self, key: str) -> bool:
        return any(k == key for _, k in self._seen)

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
        if self._in_flight or not self._allowed(now):
            self._waiting = (now, text)  # newest wins
            return False
        self._start(now, key)
        return True

    def _start(self, now: float, key: str) -> None:
        self._in_flight = True
        self._last_started = now
        self._recent_starts.append(now)
        self._seen.append((now, key))

    def finished(self, now: float) -> str | None:
        """The in-flight answer ended. Returns a waiting question to answer next, if any."""
        self._in_flight = False
        self._prune(now)
        waiting, self._waiting = self._waiting, None
        if waiting is None:
            return None
        asked_at, text = waiting
        key = _norm(text)
        if now - asked_at > self.stale_after or self._is_duplicate(key):
            return None
        if not self._allowed(now):
            # Still rate-limited: keep it waiting for the next finished()/tick().
            self._waiting = waiting
            return None
        self._start(now, key)
        return text

    def tick(self, now: float) -> str | None:
        """Periodic check: start a waiting question once limits allow."""
        if self._in_flight or self._waiting is None:
            return None
        self._in_flight = True  # finished() releases it and applies the checks
        return self.finished(now)
