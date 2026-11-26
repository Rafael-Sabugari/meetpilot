"""Run the copilot evaluation set against the real models and write a report.

    cd backend && uv run python -m evals.run                 # all cases
    uv run python -m evals.run --only int-incident meet-irb-pauta
    uv run python -m evals.run --feedback feedback.json      # + cases from your 👍 ratings
    uv run python -m evals.run --cases evals/private/cases.json   # your real cases

`evals/cases.json` uses a fictional persona (the repository is public). Real meetings and
your own profile go in `evals/private/` and reports in `evals/reports/`, both ignored by git.

Needs MEETPILOT_ANTHROPIC_API_KEY (and the answer model) in the environment. Each case costs
about one live suggestion plus a small Haiku call for the judge.
"""

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai.context import ContextSegment, build_context
from app.ai.copilot import ask
from app.core.config import Settings
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import make_llm_provider
from evals.checks import CheckResult, Expect, run_checks

HERE = Path(__file__).parent

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relevance": {"type": "integer"},
        "spoken": {"type": "integer"},
        "grounded": {"type": "integer"},
        "useful": {"type": "integer"},
        "comment": {"type": "string"},
    },
    "required": ["relevance", "spoken", "grounded", "useful", "comment"],
    "additionalProperties": False,
}

JUDGE = """You grade one suggestion a meeting copilot wrote for ONE person (the User) to say out loud. Score each criterion from 1 (bad) to 5 (excellent):
- relevance: answers exactly what was asked, in this conversation.
- spoken: sounds like a real person talking naturally (short sentences, no lists, no rehearsed phrases like "great question"), right length for the question.
- grounded: every fact about the User is supported by <profile> or the transcript; 1 if it invents experience, numbers or names.
- useful: in an interview, would impress a demanding interviewer; in a work meeting, moves the discussion forward (opinion with reason, trade-offs, risks, next step).
If a <reference> answer the User approved is given, compare against it: score useful <= 3 when the new answer is clearly worse.
comment: one short sentence in Portuguese with the main weakness (or "ok")."""


@dataclass
class Case:
    id: str
    scenario: str
    question: str
    transcript: list[tuple[str, str]]
    expect: Expect
    earlier: list[tuple[str, str]]
    reference: str | None = None


def load_cases(path: Path, only: set[str] | None) -> tuple[str, list[Case]]:
    data = json.loads(path.read_text())
    cases = [
        Case(
            id=c["id"],
            scenario=c["scenario"],
            question=c["question"],
            transcript=[(s, t) for s, t in c["transcript"]],
            expect=Expect(**c.get("expect", {})),
            earlier=[(q, a) for q, a in c.get("earlier", [])],
        )
        for c in data["cases"]
        if not only or c["id"] in only
    ]
    return data["profile"], cases


def feedback_cases(path: Path) -> list[Case]:
    """Approved (👍) suggestions exported from /brain/feedback become reference cases."""
    rows = json.loads(path.read_text())
    out = []
    for i, f in enumerate(r for r in rows if r.get("rating") == 1 and r.get("question")):
        lang = (
            "en" if f["question"].isascii() and " the " in f" {f['question'].lower()} " else "pt-BR"
        )
        out.append(
            Case(
                id=f"fb-{i + 1}",
                scenario=f.get("scenario", "interview"),
                question=f["question"],
                transcript=[("system", f["question"])],
                expect=Expect(language=lang),
                earlier=[],
                reference=f["answer"],
            )
        )
    return out


async def run_case(
    answer_llm: LLMProvider, judge_llm: LLMProvider, profile: str, case: Case
) -> dict[str, Any]:
    segs = [
        ContextSegment(source=src, text=text, start_ms=i * 5_000)
        for i, (src, text) in enumerate(case.transcript)
    ]
    lang = case.expect.language
    started = time.monotonic()
    result = await ask(
        answer_llm,
        mode="ask",
        question=case.question,
        ctx=build_context(segs, 60_000),
        answer_language=lang,
        meeting_language=lang,
        tone="natural",
        max_output_tokens=2_000,
        user_context=profile,
        earlier=case.earlier,
        scenario=case.scenario,  # type: ignore[arg-type]
    )
    seconds = time.monotonic() - started
    checks = run_checks(result.answer, case.expect)
    transcript = "\n".join(
        f"{'User' if s == 'microphone' else 'Others'}: {t}" for s, t in case.transcript
    )
    judged = await judge_llm.generate_json(
        instructions=JUDGE,
        input_text=(
            f"<scenario>{case.scenario}</scenario>\n<profile>\n{profile}\n</profile>\n"
            f"<transcript>\n{transcript}\n</transcript>\n<question>{case.question}</question>\n"
            + (f"<reference>\n{case.reference}\n</reference>\n" if case.reference else "")
            + f"<suggestion>\n{result.answer}\n</suggestion>"
        ),
        schema_name="eval_judge",
        schema=JUDGE_SCHEMA,
        max_output_tokens=300,
    )
    return {
        "case": case,
        "answer": result.answer,
        "seconds": seconds,
        "checks": checks,
        "judge": judged.data,
        "tokens": (result.input_tokens, result.output_tokens),
    }


def score(j: dict[str, Any]) -> float:
    vals = [j.get(k, 0) for k in ("relevance", "spoken", "grounded", "useful")]
    return sum(int(v) for v in vals) / 4


def report(rows: list[dict[str, Any]], model: str) -> str:
    checks: list[CheckResult] = [c for r in rows for c in r["checks"]]
    passed = sum(c.ok for c in checks)
    avg = sum(score(r["judge"]) for r in rows) / max(1, len(rows))
    secs = sorted(r["seconds"] for r in rows)
    lines = [
        f"# Avaliação do copiloto — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Modelo: `{model}` · casos: {len(rows)} · nota média do juiz: **{avg:.2f}/5** · "
        f"regras: **{passed}/{len(checks)}** · tempo mediano: {secs[len(secs) // 2]:.1f}s",
        "",
        "| caso | nota | relev. | falado | fiel | útil | regras | s | comentário |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        j = r["judge"]
        failed = [c.name for c in r["checks"] if not c.ok]
        lines.append(
            f"| {r['case'].id} | {score(j):.2f} | {j.get('relevance')} | {j.get('spoken')} | "
            f"{j.get('grounded')} | {j.get('useful')} | "
            f"{'ok' if not failed else '✗ ' + ', '.join(failed)} | {r['seconds']:.1f} | "
            f"{str(j.get('comment', '')).replace('|', '/')} |"
        )
    lines += ["", "## Respostas", ""]
    for r in rows:
        lines += [f"### {r['case'].id}", f"**Pergunta:** {r['case'].question}", "", r["answer"], ""]
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--feedback", type=Path)
    ap.add_argument("--cases", type=Path, default=HERE / "cases.json")
    args = ap.parse_args()
    settings = Settings()
    if settings.llm_provider == "fake":
        raise SystemExit("Set MEETPILOT_LLM_PROVIDER=anthropic and the API key to run evals.")
    answer_llm = make_llm_provider(settings, answers=True)
    judge_llm = make_llm_provider(settings)
    profile, cases = load_cases(args.cases, set(args.only) if args.only else None)
    if args.feedback:
        cases += feedback_cases(args.feedback)
    rows = []
    for case in cases:
        print(f"… {case.id}", flush=True)
        rows.append(await run_case(answer_llm, judge_llm, profile, case))
    model = settings.anthropic_answer_model or settings.anthropic_model
    text = report(rows, model)
    out = HERE / "reports" / f"{datetime.now():%Y%m%d-%H%M}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    print(text.split("## Respostas")[0])
    print(f"Relatório: {out}")


if __name__ == "__main__":
    asyncio.run(main())
