import type { MessageKey } from "../lib/i18n";
import { isLevelStale, dbfsToPercent } from "../lib/indicator";
import { useCapture } from "../hooks/useCapture";
import { useTranscript } from "../hooks/useTranscript";
import { recentLines } from "../lib/transcript";
import { useCopilot } from "../hooks/useCopilot";
import { AnswerCard } from "./CopilotPanel";
import { CaptureIndicator } from "./CaptureIndicator";

/**
 * Private, always-on-top overlay. In this slice it is the persistent capture indicator with
 * immediate pause/stop controls; transcript and suggestions are added in later slices.
 */
export function OverlayView({ t }: { t: (k: MessageKey) => string }) {
  const cap = useCapture();
  const transcript = useTranscript();
  const state = cap.status?.state ?? "idle";
  const lines = recentLines(transcript.state, 2);
  const copilot = useCopilot();
  const latest = copilot.state.items[0];
  const live = transcript.state.link?.state === "connected";
  return (
    <main className="overlay" data-tauri-drag-region>
      <div className="overlay__row" data-tauri-drag-region>
        <CaptureIndicator status={cap.status} demo={cap.demo} t={t} compact />
        <div className="overlay__actions">
          {state === "capturing" && (
            <button className="btn btn--small" onClick={cap.pause} title={t("controls.pause")} aria-label={t("controls.pause")}>❚❚</button>
          )}
          {state === "paused" && (
            <button className="btn btn--small" onClick={cap.resume} title={t("controls.resume")} aria-label={t("controls.resume")}>▶</button>
          )}
          {state !== "idle" && (
            <button className="btn btn--small btn--danger" onClick={cap.stop} title={t("controls.stop")} aria-label={t("controls.stop")}>■</button>
          )}
        </div>
      </div>
      <div className="overlay__meters" data-tauri-drag-region>
        {(cap.status?.sources ?? []).map((s) => {
          const lvl = cap.levels[s.source];
          const pct = s.state === "capturing" && !isLevelStale(lvl?.at, cap.now) && lvl ? dbfsToPercent(lvl.reading.rms_dbfs) : 0;
          return (
            <div key={s.source} className="mini-meter" title={s.source}>
              <span>{s.source === "system" ? "🔊" : "🎙"}</span>
              <div className="meter__track"><div className="meter__fill" style={{ width: `${pct}%` }} /></div>
            </div>
          );
        })}
      </div>
      {(live || latest) && (
        <div className="overlay__copilot">
          {live && (
            <button
              className="btn btn--small"
              disabled={copilot.pending}
              onClick={() => copilot.ask("reply", null, "natural", transcript.state)}
            >
              💬 {t("copilot.reply")}
            </button>
          )}
          {latest && <AnswerCard t={t} item={latest} compact />}
        </div>
      )}
      {lines.length > 0 && (
        <div className="overlay__lines" aria-live="polite">
          {lines.map((l, i) => (
            <p key={i} className={l.final ? "" : "line--partial"}>
              <span className="who">{l.source === "system" ? t("tx.others") : t("tx.you")}</span>
              {l.text}
            </p>
          ))}
        </div>
      )}
    </main>
  );
}
