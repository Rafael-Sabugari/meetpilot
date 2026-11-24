"""Local search over the second brain (no embeddings, no external calls).

Memories are split into passages and ranked with BM25 plus a small recency boost. A
personal brain holds hundreds of memories, not millions, so this runs in milliseconds and
keeps every word on the User's own server.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

PASSAGE_CHARS = 1_200

STOPWORDS = frozenset(
    """
    a o as os um uma uns umas de do da dos das no na nos nas em por para pra pro com sem
    e ou mas que se como quando onde qual quais quem porque pq ja nao sim mais menos muito
    muita muitos muitas pouco eu voce vc ele ela eles elas nos me te lhe meu minha meus minhas
    seu sua seus suas esse essa isso este esta isto aquele aquela aquilo ser estar ter foi era
    sao esta estao tem tinha fazer feito vai vou ao aos pelo pela pelos pelas sobre entre
    ate apos tambem so ainda entao ai la aqui cada todo toda todos todas outro outra
    the an and or but of to in on at for with without by from as is are was were be been
    being have has had do does did i you he she it we they me my your his her its our their
    this that these those what which who whom when where why how not no yes can could would
    should will just also about into than then there here so if
    """.split()  # noqa: SIM905
)

_WORD = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


def fold(text: str) -> str:
    """Lowercase without accents ("Reunião" -> "reuniao")."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(fold(text)):
        if w in STOPWORDS or len(w) < 2:
            continue
        out.append(w)
        # "trigger.new" also matches "trigger"; "follow-up" also matches "follow".
        if any(c in w for c in "._-"):
            out.extend(p for p in re.split(r"[._-]", w) if len(p) > 1 and p not in STOPWORDS)
    return out


@dataclass(frozen=True)
class MemoryDoc:
    """What search needs from a memory (decoupled from the ORM row)."""

    id: str
    kind: str
    title: str
    occurred_at: datetime
    text: str


@dataclass(frozen=True)
class Passage:
    memory_id: str
    kind: str
    title: str
    occurred_at: datetime
    text: str


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float


def split_passages(doc: MemoryDoc, size: int = PASSAGE_CHARS) -> list[Passage]:
    """Paragraph-aligned chunks of about `size` characters (a long paragraph is cut)."""
    chunks: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n|\n(?=[#*-] )", doc.text.strip()):
        para = para.strip()
        while len(para) > size:
            if current:
                chunks.append(current)
                current = ""
            cut = para.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return [Passage(doc.id, doc.kind, doc.title, doc.occurred_at, c) for c in chunks or [doc.title]]


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def search(
    docs: Iterable[MemoryDoc],
    query: str,
    *,
    limit: int = 6,
    now: datetime | None = None,
    strict: bool = False,
) -> list[Hit]:
    """Best passages for `query` (BM25, title words count double, newer slightly ahead).

    `strict` keeps only clear matches: at least two different query words, or one that is
    in the memory's title (for live answers, where an unrelated memory is just noise)."""
    passages = [p for d in docs for p in split_passages(d)]
    q = list(dict.fromkeys(tokens(query)))
    if not passages or not q:
        return []
    bags = [Counter(tokens(p.title) * 2 + tokens(p.text)) for p in passages]
    n = len(passages)
    avg = sum(sum(b.values()) for b in bags) / n or 1.0
    df = {t: sum(1 for b in bags if t in b) for t in q}
    k1, b = 1.4, 0.75
    now = now or datetime.now(UTC)
    hits: list[Hit] = []
    for p, bag in zip(passages, bags, strict=True):
        length = sum(bag.values()) or 1
        score = 0.0
        for t in q:
            tf = bag.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        if score <= 0:
            continue
        if strict:
            matched = {t for t in q if t in bag}
            if len(matched) < 2 and not matched & set(tokens(p.title)):
                continue
        age_days = max(0.0, (now - _aware(p.occurred_at)).total_seconds() / 86_400)
        score *= 1 + 0.15 / (1 + age_days / 30)
        hits.append(Hit(p, score))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def recent(docs: Sequence[MemoryDoc], limit: int = 5) -> list[Hit]:
    """Newest memories (first passage of each), for questions with no searchable words."""
    newest = sorted(docs, key=lambda d: _aware(d.occurred_at), reverse=True)[:limit]
    return [Hit(split_passages(d)[0], 0.0) for d in newest]


def render(hits: Sequence[Hit], max_chars: int) -> str:
    """`<memory>` blocks for a prompt, best first, within the character budget."""
    out: list[str] = []
    used = 0
    for h in hits:
        p = h.passage
        block = (
            f'<memory id="{p.memory_id}" kind="{p.kind}" '
            f'date="{_aware(p.occurred_at).date().isoformat()}" '
            f'title="{_attr(p.title)}">\n{p.text}\n</memory>'
        )
        if used + len(block) > max_chars:
            break
        out.append(block)
        used += len(block) + 1
    return "\n".join(out)


def _attr(text: str) -> str:
    return " ".join(text.split()).replace('"', "'")[:200]
