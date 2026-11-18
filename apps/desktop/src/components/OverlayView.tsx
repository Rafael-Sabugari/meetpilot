import type { MessageKey } from "../lib/i18n";
import { isLevelStale, dbfsToPercent } from "../lib/indicator";
import { useCapture } from "../hooks/useCapture";
import { useTranscript } from "../hooks/useTranscript";
import { recentLines } from "../lib/transcript";
import { useCopilot } from "../hooks/useCopilot";
import { AnswerCard } from "./CopilotPanel";
import type { CopilotItem } from "../lib/copilot";
import { useState } from "react";
import type { CopilotTone } from "@meetpilot/shared-types";
import { api } from "../lib/api";
import { loadLivePrefs, saveLivePrefs, toLiveOptions } from "../lib/liveOptions";
import { CaptureIndicator } from "./CaptureIndicator";
import { startOverlayDrag } from "../lib/overlayDrag";

/**
 * Private, always-on-top overlay: capture indicator with immediate pause/stop, the last
 * transcript lines (with translation) and every suggestion of the session (newest first,
 * scrollable), including the ones made automatically when someone asks the user a question.
 */
export function OverlayView({ t }: { t: (k: MessageKey) => string }) {
  const cap = useCapture();
  const transcript = useTranscript();
  const state = cap.status?.state ?? "idle";
  const lines = recentLines(transcript.state, 2);
  const copilot = useCopilot();
  const items = copilot.state.items;
  const live = transcript.state.link?.state === "connected";
  // Read on every render: cheap, and picks up changes made in the main window.
  const [, rerender] = useState(0);
  const prefs = loadLivePrefs();
  const transcribing = !!(cap.status as { transcribing?: boolean } | null)?.transcribing;
  const setTone = (tone: CopilotTone) => {
    const next = { ...loadLivePrefs(), tone };
    saveLivePrefs(next);
    rerender((n) => n + 1);
    // Applies to the next suggestions; answers already given stay as they are.
    if (transcribing) api.setLiveOptions(toLiveOptions(next)).catch(() => {});
  };
  return (
    <main className="overlay" onMouseDown={startOverlayDrag}>
      <div className="overlay__grip" aria-hidden="true" title={t("overlay.drag")}>
        ⠿⠿⠿
      </div>
      <div className="overlay__row">
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
      <div className="overlay__meters">
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
      {(live || items.length > 0) && (
        <div className="overlay__copilot">
          <div className="overlay__ask">
            {live && (
              <button
                className="btn btn--small"
                disabled={copilot.pending}
                onClick={() => copilot.ask("reply", null, prefs.tone, transcript.state, prefs.userContext)}
              >
                💬 {t("copilot.reply")}
              </button>
            )}
            <OverlayTone t={t} tone={prefs.tone} onChange={setTone} />
          </div>
          <OverlayAnswers t={t} items={items} />
        </div>
      )}
      {lines.length > 0 && (
        <div className="overlay__lines" aria-live="polite">
          {lines.map((l, i) => {
            const tr = prefs.translationView !== "original" ? l.translation : undefined;
            return (
              <p key={i} className={l.final ? "" : "line--partial"}>
                <span className="who">{l.source === "system" ? t("tx.others") : t("tx.you")}</span>
                {(!tr || prefs.translationView === "both") && l.text}
                {tr && <span className={prefs.translationView === "both" ? "line__tr" : ""}>{tr}</span>}
              </p>
            );
          })}
        </div>
      )}
    </main>
  );
}

/** All suggestions, newest on top; a new question never hides the earlier answers. */
export function OverlayAnswers({ t, items }: { t: (k: MessageKey) => string; items: CopilotItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="overlay__answers" data-no-drag data-testid="overlay-answers">
      {items.map((item, i) => (
        <div key={item.id} className={i === 0 ? "overlay__answer overlay__answer--latest" : "overlay__answer"}>
          <AnswerCard t={t} item={item} compact />
        </div>
      ))}
    </div>
  );
}

const TONES: CopilotTone[] = ["natural", "concise", "technical", "formal"];

/** How the next suggestions should sound; earlier answers are not changed. */
export function OverlayTone(props: {
  t: (k: MessageKey) => string;
  tone: CopilotTone;
  onChange: (tone: CopilotTone) => void;
}) {
  const { t, tone, onChange } = props;
  return (
    <label className="overlay__tone" title={t("overlay.toneHint")}>
      <span>{t("copilot.tone")}</span>
      <select aria-label={t("copilot.tone")} value={tone} onChange={(e) => onChange(e.target.value as CopilotTone)}>
        {TONES.map((x) => (
          <option key={x} value={x}>
            {t(`copilot.tone.${x}` as MessageKey)}
          </option>
        ))}
      </select>
    </label>
  );
}
