import { useState } from "react";
import type { CopilotTone, Grounding } from "@meetpilot/shared-types";
import type { MessageKey } from "../lib/i18n";
import type { CopilotItem } from "../lib/copilot";
import type { TranscriptState } from "../lib/transcript";
import type { useCopilot } from "../hooks/useCopilot";

type T = (k: MessageKey) => string;

const TONES: CopilotTone[] = ["natural", "concise", "technical", "formal"];

export function GroundingBadge({ t, grounding }: { t: T; grounding: Grounding }) {
  const tone = grounding === "needs_confirmation" ? "warn" : grounding === "from_conversation" ? "ok" : "muted";
  return (
    <span className={`pill pill--${tone}`} data-testid="grounding">
      {t(`copilot.grounding.${grounding}` as MessageKey)}
    </span>
  );
}

export function AnswerCard({ t, item, compact }: { t: T; item: CopilotItem; compact?: boolean }) {
  const [copied, setCopied] = useState(false);
  const auto = item.mode === "auto";
  const title = item.mode === "ask" ? item.question : t(`copilot.mode.${item.mode}` as MessageKey);
  return (
    <article className={`answer${compact ? " answer--compact" : ""}${auto ? " answer--auto" : ""}`} aria-live="polite">
      {auto ? (
        <p className="answer__q" data-testid="auto-question">
          <span className="pill pill--accent">{t("copilot.mode.auto")}</span> {item.question}
        </p>
      ) : (
        !compact && <p className="answer__q">{title}</p>
      )}
      {item.status === "pending" && <p className="muted">{t("copilot.thinking")}</p>}
      {item.status === "failed" && <p className="error-text">{item.error}</p>}
      {item.status === "done" && item.answer && (
        <>
          <p className="answer__text">{item.answer.answer}</p>
          <div className="row">
            <GroundingBadge t={t} grounding={item.answer.grounding} />
            <button
              className="btn btn--small"
              onClick={() =>
                navigator.clipboard?.writeText(item.answer!.answer).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                })
              }
            >
              {copied ? t("copilot.copied") : t("copilot.copy")}
            </button>
          </div>
          {!compact && item.answer.explanation && <p className="muted small">{item.answer.explanation}</p>}
          {!compact && item.answer.missing_info.length > 0 && (
            <p className="small warn-text">
              {t("copilot.missing")}: {item.answer.missing_info.join(" · ")}
            </p>
          )}
        </>
      )}
    </article>
  );
}

export function CopilotPanel(props: {
  t: T;
  copilot: ReturnType<typeof useCopilot>;
  transcript: TranscriptState;
  enabled: boolean;
  /** Shared with automatic suggestions, so it lives in the parent. */
  tone: CopilotTone;
  onToneChange: (tone: CopilotTone) => void;
}) {
  const { t, copilot, transcript, enabled, tone, onToneChange: setTone } = props;
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const latest = copilot.state.items.find((i) => i.status === "done")?.answer;

  const run = async (mode: "ask" | "reply" | "summarize") => {
    setError(null);
    const q = mode === "ask" ? question.trim() : null;
    if (mode === "ask" && !q) return;
    const err = await copilot.ask(mode, q, tone, transcript);
    if (err) setError(err);
    else if (mode === "ask") setQuestion("");
  };

  return (
    <section className="card card--wide">
      <div className="row-between">
        <h2>{t("copilot.title")}</h2>
        <label className="row small">
          <span className="muted">{t("copilot.tone")}</span>
          <select value={tone} onChange={(e) => setTone(e.target.value as CopilotTone)}>
            {TONES.map((x) => (
              <option key={x} value={x}>{t(`copilot.tone.${x}` as MessageKey)}</option>
            ))}
          </select>
        </label>
      </div>
      {!enabled && <p className="muted">{t("copilot.needsMeeting")}</p>}
      {latest?.provider === "fake" && <p className="notice small">{t("copilot.fake")}</p>}
      <div className="row wrap">
        <button className="btn" disabled={!enabled || copilot.pending} onClick={() => run("reply")}>
          {t("copilot.reply")}
        </button>
        <button className="btn" disabled={!enabled || copilot.pending} onClick={() => run("summarize")}>
          {t("copilot.summarize")}
        </button>
      </div>
      <form
        className="row ask"
        onSubmit={(e) => {
          e.preventDefault();
          run("ask");
        }}
      >
        <input
          aria-label={t("copilot.placeholder")}
          placeholder={t("copilot.placeholder")}
          value={question}
          maxLength={1000}
          disabled={!enabled}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button className="btn btn--primary" type="submit" disabled={!enabled || copilot.pending || !question.trim()}>
          {t("copilot.ask")}
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
      {copilot.state.items.length === 0 ? (
        <p className="muted small">{t("copilot.empty")}</p>
      ) : (
        <div className="answers">
          {copilot.state.items.map((item) => (
            <AnswerCard key={item.id} t={t} item={item} />
          ))}
        </div>
      )}
      <p className="muted small">
        {t("copilot.disclaimer")}
        {latest && ` · ${latest.requests_remaining} ${t("copilot.remaining")}`}
      </p>
    </section>
  );
}
