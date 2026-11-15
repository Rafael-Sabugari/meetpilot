import { useEffect, useRef, useState } from "react";
import type { LinkStatus } from "@meetpilot/shared-types";
import type { MessageKey } from "../lib/i18n";
import { formatClock, transcriptAsText, type TranscriptState } from "../lib/transcript";

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

export function TranscriptPanel(props: { t: T; state: TranscriptState; droppedFrames: number }) {
  const { t, state, droppedFrames } = props;
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
        {state.finals.map((s) => (
          <p key={`${s.segment_id}-${s.end_ms}`} className={`line line--${s.source}`}>
            <span className="line__time">{formatClock(s.start_ms)}</span>
            <span className="line__who">{labels[s.source]}</span>
            <span>{s.text}</span>
          </p>
        ))}
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
