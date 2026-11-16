import type { DownloadEvent, LocalModels, SttEngine } from "../lib/api";
import type { MessageKey } from "../lib/i18n";

type T = (k: MessageKey) => string;

const MB = 1024 * 1024;

/** The model offered for free transcription (recommended by the engine). */
export function pickModel(models: LocalModels | null) {
  return models?.models.find((m) => m.recommended) ?? models?.models[0] ?? null;
}

/** Choose who turns speech into text: Deepgram (cloud, paid) or Whisper on this computer. */
export function TranscriptionEngine(props: {
  t: T;
  engine: SttEngine;
  onChange: (e: SttEngine) => void;
  models: LocalModels | null;
  progress: DownloadEvent | null;
  error: string | null;
  onDownload: (id: string) => void;
  onCancel: (id: string) => void;
  disabled: boolean;
}) {
  const { t, engine, onChange, models, progress, error, onDownload, onCancel, disabled } = props;
  if (!models?.available) return null;
  const model = pickModel(models);
  const pct = progress?.total ? Math.round((progress.downloaded / progress.total) * 100) : null;
  return (
    <div className="engine" data-testid="stt-engine">
      <label className="select">
        <span className="muted small">{t("stt.engine")}</span>
        <select aria-label={t("stt.engine")} value={engine} disabled={disabled} onChange={(e) => onChange(e.target.value as SttEngine)}>
          <option value="cloud">{t("stt.cloud")}</option>
          <option value="local">{t("stt.local")}</option>
        </select>
      </label>
      {engine === "local" && model && (
        <div className="engine__model">
          {model.downloaded ? (
            <p className="muted small" data-testid="model-ready">✓ {t("stt.ready")}</p>
          ) : progress ? (
            <div className="row">
              <div className="meter__track engine__bar" aria-label={t("stt.downloading")}>
                <div className="meter__fill" style={{ width: `${pct ?? 0}%` }} />
              </div>
              <span className="muted small">
                {pct !== null ? `${pct}%` : t("stt.downloading")} · {Math.round(progress.downloaded / MB)} MB
              </span>
              <button className="btn btn--small" onClick={() => onCancel(model.id)}>{t("stt.cancel")}</button>
            </div>
          ) : (
            <button className="btn btn--small" disabled={disabled} onClick={() => onDownload(model.id)}>
              ⤓ {t("stt.download")} (~{Math.round(model.approx_bytes / MB)} MB)
            </button>
          )}
          {error && <p className="error small">{error}</p>}
          <p className="muted small">{t("stt.localHint")}</p>
        </div>
      )}
    </div>
  );
}
