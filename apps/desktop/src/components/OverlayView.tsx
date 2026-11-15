import type { MessageKey } from "../lib/i18n";
import { isLevelStale, dbfsToPercent } from "../lib/indicator";
import { useCapture } from "../hooks/useCapture";
import { CaptureIndicator } from "./CaptureIndicator";

/**
 * Private, always-on-top overlay. In this slice it is the persistent capture indicator with
 * immediate pause/stop controls; transcript and suggestions are added in later slices.
 */
export function OverlayView({ t }: { t: (k: MessageKey) => string }) {
  const cap = useCapture();
  const state = cap.status?.state ?? "idle";
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
    </main>
  );
}
