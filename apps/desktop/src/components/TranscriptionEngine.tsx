import type { AppleStatus, DownloadEvent, LocalModels, SttEngine } from "../lib/api";
import type { MessageKey } from "../lib/i18n";

type T = (k: MessageKey) => string;

const MB = 1024 * 1024;

/** The chosen model if it exists, else the recommended one. */
export function pickModel(models: LocalModels | null, preferred = "") {
  return (
    models?.models.find((m) => m.id === preferred) ??
    models?.models.find((m) => m.recommended) ??
    models?.models[0] ??
    null
  );
}

/** Speed/accuracy label per model id (others show their id). */
const MODEL_LABEL: Record<string, MessageKey> = {
  "large-v3-turbo-q5_0": "stt.model.accurate",
  small: "stt.model.fast",
  base: "stt.model.fastest",
};
const MODEL_ORDER = ["large-v3-turbo-q5_0", "small", "base"];
/** Apple's on-device engine (macOS 26+), offered next to the Whisper models. */
export const APPLE_MODEL = "apple";

/** Choose who turns speech into text: Deepgram (cloud, paid) or Whisper on this computer. */
export function TranscriptionEngine(props: {
  t: T;
  engine: SttEngine;
  onChange: (e: SttEngine) => void;
  models: LocalModels | null;
  modelId: string;
  onModelChange: (id: string) => void;
  progress: DownloadEvent | null;
  error: string | null;
  onDownload: (id: string) => void;
  onCancel: (id: string) => void;
  disabled: boolean;
  apple?: AppleStatus | null;
}) {
  const { t, engine, onChange, models, modelId, onModelChange, progress, error, onDownload, onCancel, disabled, apple } = props;
  if (!models?.available) return null;
  const useApple = modelId === APPLE_MODEL && !!apple?.available;
  const model = pickModel(models, modelId);
  const ordered = [...models.models].sort((a, b) => MODEL_ORDER.indexOf(a.id) - MODEL_ORDER.indexOf(b.id));
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
          <label className="select">
            <span className="muted small">{t("stt.model")}</span>
            <select
              aria-label={t("stt.model")}
              value={useApple ? APPLE_MODEL : model.id}
              disabled={disabled || !!progress}
              onChange={(e) => onModelChange(e.target.value)}
            >
              {apple?.available && <option value={APPLE_MODEL}>{t("stt.model.apple")}</option>}
              {ordered.map((m) => (
                <option key={m.id} value={m.id}>
                  {MODEL_LABEL[m.id] ? t(MODEL_LABEL[m.id]) : m.id} · {Math.round(m.approx_bytes / MB)} MB
                  {m.downloaded ? " ✓" : ""}
                </option>
              ))}
            </select>
          </label>
          {useApple ? (
            <p className="muted small" data-testid="model-ready">✓ {t("stt.apple.ready")}</p>
          ) : model.downloaded ? (
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
          {apple && !apple.available && apple.reason && (
            <p className="muted small" data-testid="apple-unavailable">
              {t("stt.apple.unavailable")}: {apple.reason}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
