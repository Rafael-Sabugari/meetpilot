import { useEffect, useState } from "react";
import type { MessageKey } from "../lib/i18n";
import { MAX_USER_CONTEXT } from "../lib/liveOptions";

type T = (k: MessageKey) => string;

/**
 * "My context": what the user wants the copilot to know about them and the meeting.
 * Saved only on this computer; committed when the field loses focus (or on Save), so a
 * running meeting gets one update instead of one per keystroke.
 */
export function UserContextCard(props: { t: T; value: string | null; onSave: (v: string | null) => void }) {
  const { t, value, onSave } = props;
  const [draft, setDraft] = useState(value ?? "");
  const [committed, setCommitted] = useState(value ?? "");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setDraft(value ?? "");
    setCommitted(value ?? "");
  }, [value]);
  const dirty = draft.trim() !== committed.trim();
  const commit = () => {
    if (!dirty) return;
    const next = draft.trim().slice(0, MAX_USER_CONTEXT);
    setCommitted(next);
    onSave(next || null);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };
  return (
    <section className="card card--wide" data-testid="user-context">
      <div className="row-between">
        <h2>{t("ctx.title")}</h2>
        <span className="muted small">
          {draft.length}/{MAX_USER_CONTEXT}
        </span>
      </div>
      <p className="muted small">{t("ctx.help")}</p>
      <textarea
        aria-label={t("ctx.title")}
        className="ctx-input"
        rows={5}
        maxLength={MAX_USER_CONTEXT}
        placeholder={t("ctx.placeholder")}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
      />
      <div className="row-between">
        <p className="muted small">{t("ctx.privacy")}</p>
        <button className="btn btn--small" disabled={!dirty} onClick={commit}>
          {saved ? t("ctx.saved") : t("ctx.save")}
        </button>
      </div>
    </section>
  );
}
