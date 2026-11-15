import { useState } from "react";
import type { MessageKey } from "../lib/i18n";

export const CONSENT_KEY = "meetpilot.consent.transcription.v1";

export function hasTranscriptionConsent(): boolean {
  try {
    return localStorage.getItem(CONSENT_KEY) !== null;
  } catch {
    return false;
  }
}

export function recordTranscriptionConsent(): void {
  try {
    localStorage.setItem(CONSENT_KEY, new Date().toISOString());
  } catch {
    /* storage unavailable: the dialog will be shown again next time */
  }
}

export function ConsentDialog(props: { t: (k: MessageKey) => string; onAccept: () => void; onCancel: () => void }) {
  const { t, onAccept, onCancel } = props;
  const [checked, setChecked] = useState(false);
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="consent-title">
      <div className="modal__body card">
        <h2 id="consent-title">{t("consent.title")}</h2>
        <p>{t("consent.what")}</p>
        <p>{t("consent.retention")}</p>
        <p>{t("consent.others")}</p>
        <label className="check">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          {t("consent.check")}
        </label>
        <div className="controls">
          <button
            className="btn btn--primary"
            disabled={!checked}
            onClick={() => {
              recordTranscriptionConsent();
              onAccept();
            }}
          >
            {t("consent.accept")}
          </button>
          <button className="btn" onClick={onCancel}>{t("consent.cancel")}</button>
        </div>
      </div>
    </div>
  );
}
