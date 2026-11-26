import { useEffect, useRef, useState } from "react";
import type { LinkStatus } from "@meetpilot/shared-types";
import type { MessageKey } from "../lib/i18n";
import { formatClock, transcriptAsText, type TranscriptState, type TranslationView } from "../lib/transcript";

type T = (k: MessageKey) => string;

export function linkLabel(t: T, link: LinkStatus | null): { text: string; tone: string } | null {
  if (!link) return null;
  switch (link.state) {
    case "connecting":
      return { text: t("tx.link.connecting"), tone: "warn" };
    case "connected":
      return { text: t("tx.link.connected"), tone: "ok" };
    case "reconnecting":
      return { text: `${t("tx.link.reconnecting")} (#${link.attempt})`, tone: "warn" };
    case "closed":
      return { text: t("tx.link.closed"), tone: "muted" };
    case "failed":
      return { text: `${t("tx.link.failed")}: ${link.reason}`, tone: "bad" };
  }
}

const VIEWS: TranslationView[] = ["both", "translation", "original"];

/** A stable colour per speaker label ("Pessoa 2" -> 2). */
export function speakerTone(speaker: string): number {
  const n = Number(speaker.replace(/\D+/g, ""));
  return Number.isFinite(n) && n > 0 ? ((n - 1) % 6) + 1 : 1;
}

export function TranscriptPanel(props: {
  t: T;
  state: TranscriptState;
  droppedFrames: number;
  view?: TranslationView;
  onViewChange?: (v: TranslationView) => void;
}) {
  const { t, state, droppedFrames, view = "both", onViewChange } = props;
  const hasTranslations = Object.keys(state.translations).length > 0;
  const labels = { system: t("tx.others"), microphone: t("tx.you") };
  const end = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [state.finals.length, state.partials]);
  const link = linkLabel(t, state.link);
  const partials = Object.values(state.partials).filter(Boolean);

  return (
    <section className="card card--wide">
      <div className="row-between">
        <h2>{t("tx.title")}</h2>
        <div className="row">
          {hasTranslations && onViewChange && (
            <select aria-label={t("live.view")} value={view} onChange={(e) => onViewChange(e.target.value as TranslationView)}>
              {VIEWS.map((v) => (
                <option key={v} value={v}>{t(`live.view.${v}` as MessageKey)}</option>
              ))}
            </select>
          )}
          {link && <span className={`pill pill--${link.tone}`} data-testid="link-status">{link.text}</span>}
          <button
            className="btn btn--small"
            disabled={state.finals.length === 0}
            onClick={() => {
              navigator.clipboard?.writeText(transcriptAsText(state, labels)).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
          >
            {copied ? t("tx.copied") : t("tx.copy")}
          </button>
        </div>
      </div>
      {state.provider === "fake" && <p className="notice small">{t("tx.fakeProvider")}</p>}
      {droppedFrames > 0 && <p className="error-text small">{droppedFrames} {t("tx.dropped")}</p>}
      {state.errors.map((e, i) => (
        <p key={i} className="error-text small">{e.message}</p>
      ))}
      <div className="transcript" aria-live="polite">
        {state.finals.length === 0 && partials.length === 0 && <p className="muted">{t("tx.empty")}</p>}
        {state.finals.map((s) => {
          const tr = state.translations[s.segment_id];
          const showOriginal = !tr || view !== "translation";
          const showTranslation = !!tr && view !== "original";
          const question = state.questions.includes(s.segment_id);
          return (
            <div key={`${s.segment_id}-${s.end_ms}`} className={`line line--${s.source}${question ? " line--question" : ""}`}>
              <span className="line__time">{formatClock(s.start_ms)}</span>
              <span className={`line__who${s.speaker && s.source === "system" ? ` line__who--${speakerTone(s.speaker)}` : ""}`}>
                {(s.source === "system" && s.speaker) || labels[s.source]}
              </span>
              <span className="line__body">
                {question && <span className="pill pill--accent small">{t("live.question")}</span>}{" "}
                {showOriginal && <span>{s.text}</span>}
                {showTranslation && (
                  <span className={showOriginal ? "line__tr" : ""} data-testid="translation">
                    {tr}
                  </span>
                )}
              </span>
            </div>
          );
        })}
        {partials.map((s) => (
          <p key={`partial-${s!.source}`} className={`line line--${s!.source} line--partial`}>
            <span className="line__time">{formatClock(s!.start_ms)}</span>
            <span className="line__who">{labels[s!.source]}</span>
            <span>{s!.text}</span>
          </p>
        ))}
        <div ref={end} />
      </div>
    </section>
  );
}
