"""Drop transcription garbage before it is shown or sent to the AI.

Local Whisper sometimes "hallucinates" on silence or faint background sound: one token
repeated over and over ("s, the.s, the.s, the.s…"), or a string of nonsense fragments with
letters from other alphabets ("W-icкм. all gone Currys dob. sheesh serso."). Real speech is
kept; only clearly broken text is dropped.
"""

import re
import unicodedata
from collections import Counter

_WORD = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*", re.UNICODE)
_SENTENCE = re.compile(r"[.!?…]+")
# "J- Transd-als »-Pass-lo- Sch-": word pieces cut off with a dangling hyphen.
_DANGLING = re.compile(r"\w-(?=\s|$)|»-")
# Single letters that are not words in English or Portuguese ("t", "p", "D" in noise).
_LONE_LETTER_WORDS = frozenset(["a", "i", "e", "o", "y", "à", "é"])


def _script(ch: str) -> str:
    name = unicodedata.name(ch, "")
    return name.split(" ", 1)[0]  # LATIN, CYRILLIC, GREEK, CJK, ARABIC...


def _mixed_script_word(word: str) -> bool:
    scripts = {_script(c) for c in word if c.isalpha()}
    return len(scripts) > 1


def is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    words = [w.lower() for w in _WORD.findall(t)]
    if not words:
        return True  # only punctuation, digits or symbols ("...", "♪", "3.0")
    # Letters from two alphabets inside one word never happen in real speech.
    if any(_mixed_script_word(w) for w in words):
        return True
    if len(_DANGLING.findall(t)) >= 3:
        return True
    lone = sum(1 for w in words if len(w) == 1 and w not in _LONE_LETTER_WORDS)
    if lone >= 3:
        return True  # "and iring.WG.yes of t shoulders.D. p, shoot! kry,y,"
    n = len(words)
    if n >= 6:
        top = Counter(words).most_common(1)[0][1]
        if top / n >= 0.5 or len(set(words)) / n <= 0.3:
            return True  # the same token or short loop repeated
    # Many one-word "sentences" in a row: fragments, not speech.
    pieces = [p for p in _SENTENCE.split(t) if _WORD.search(p)]
    return len(pieces) >= 6 and n / len(pieces) <= 1.7


# -- cleanup inside a segment ------------------------------------------------------------------
# Whisper can glue garbage onto real speech ("…a gente fala. 1.1.1.1.1 香港,,,, do… do… do…").
# `clean` removes those pieces and keeps the rest; `is_noise` then judges what is left.

_NUMBER_CHAIN = re.compile(r"\b\d+(?:[.,]\d+){3,}\.?")  # 1.1.1.1.1 / 2.1.2.1.1
_NUMBER_RUN = re.compile(r"(?:\b\d{1,2}[.,]?(?:\s+|$)){4,}")  # 1. 1. 1. 1. / 4, 5, 6, 6,
# The same word or short phrase three or more times in a row ("do… do… do…", "ARQUI, ARQUI").
_REPEAT = re.compile(
    r"\b([^\W\d_]+(?:\s+[^\W\d_]+){0,2})(?:[\s,.;:!?…-]+\1\b){2,}", re.IGNORECASE | re.UNICODE
)
_PUNCT_RUN = re.compile(r"([,;:])(?:\s*[,;:])+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?…])")


def clean(text: str) -> str:
    """Remove obvious recognition garbage from a pt/en segment, keeping the real speech."""
    t = text.replace("�", "")
    # Meetings are in Portuguese or English: letters from other alphabets are recognition
    # errors (香港, 아주, кк).
    t = "".join(c for c in t if not (c.isalpha() and _script(c) != "LATIN"))
    t = _NUMBER_CHAIN.sub(" ", t)
    t = _NUMBER_RUN.sub(" ", t)
    t = _REPEAT.sub(r"\1", t)
    t = _PUNCT_RUN.sub(r"\1", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,;:")
