"""Learning from the User's ratings, without training a model.

Suggestions the User approved (👍) are shown to the copilot as examples to imitate (length,
tone, way of speaking); clearly related ones the User rejected (👎) as examples to avoid.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.search import MemoryDoc, search
from app.db.models import SuggestionFeedback

MAX_LOADED = 300
MAX_GOOD = 3
MAX_BAD = 2
MAX_EXAMPLE_CHARS = 1_200


@dataclass(frozen=True)
class Example:
    question: str
    answer: str
    rating: int


async def load_examples(db: AsyncSession, user_id: str, scenario: str) -> list[Example]:
    rows = await db.execute(
        select(SuggestionFeedback)
        .where(SuggestionFeedback.user_id == user_id, SuggestionFeedback.scenario == scenario)
        .order_by(SuggestionFeedback.created_at.desc())
        .limit(MAX_LOADED)
    )
    return [Example(f.question, f.answer, f.rating) for f in rows.scalars()]


def pick(examples: Sequence[Example], question: str) -> tuple[list[Example], list[Example]]:
    """Approved examples (related ones first, else the newest, for style) and related
    rejected ones."""
    now = datetime.now(UTC)
    docs = [
        MemoryDoc(str(i), "example", e.question or "-", now, e.answer)
        for i, e in enumerate(examples)
    ]
    hits = search(docs, question, limit=20, strict=True)
    ordered = list(dict.fromkeys(int(h.passage.memory_id) for h in hits))
    good = [examples[i] for i in ordered if examples[i].rating > 0][:MAX_GOOD]
    if len(good) < 2:
        for e in examples:  # newest first: the User's current style
            if e.rating > 0 and e not in good:
                good.append(e)
            if len(good) >= 2:
                break
    bad = [examples[i] for i in ordered if examples[i].rating < 0][:MAX_BAD]
    return good, bad


async def examples_for(db: AsyncSession, user_id: str, scenario: str, question: str) -> str | None:
    good, bad = pick(await load_examples(db, user_id, scenario), question)
    return render_examples(good, bad)


def render_examples(good: Sequence[Example], bad: Sequence[Example]) -> str | None:
    def block(tag: str, items: Sequence[Example]) -> str:
        return "\n".join(
            f"<{tag}>\nQuestion: {' '.join(e.question.split())[:400]}\n"
            f"Suggestion: {e.answer.strip()[:MAX_EXAMPLE_CHARS]}\n</{tag}>"
            for e in items
        )

    parts = [b for b in (block("approved", good), block("rejected", bad)) if b]
    return "\n".join(parts) or None
