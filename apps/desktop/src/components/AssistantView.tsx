import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { MessageKey } from "../lib/i18n";
import { isTauri, toCommandError } from "../lib/api";
import { brain, formatDate } from "../lib/brain";
import { historyFor, loadChat, saveChat, type AssistantMsg } from "../lib/assistant";
import { loadLivePrefs } from "../lib/liveOptions";
import { startOverlayDrag } from "../lib/overlayDrag";

const hide = () => {
  if (isTauri()) void getCurrentWindow().hide().catch(() => {});
};

/**
 * The shortcut assistant (Cmd/Ctrl+Shift+Space): ask anything by text, from any app.
 * Knows your meetings and notes (second brain) and "My context", plus general knowledge.
 */
export function AssistantView(props: { t: (k: MessageKey) => string }) {
  const { t } = props;
  const [chat, setChat] = useState<AssistantMsg[]>(() => loadChat());
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);

  const focus = useCallback(() => setTimeout(() => input.current?.focus(), 30), []);
  useEffect(() => {
    focus();
    if (!isTauri()) return;
    const off = listen("app://assistant-open", () => {
      // Pick up a context edited in the main window since last time.
      setChat(loadChat());
      focus();
    });
    return () => void off.then((f) => f());
  }, [focus]);
  useEffect(() => saveChat(chat), [chat]);
  useEffect(() => end.current?.scrollIntoView?.({ block: "end" }), [chat, busy]);

  const send = async () => {
    const question = draft.trim();
    if (!question || busy) return;
    const now = Date.now();
    const history = historyFor(chat, now);
    const base = history.length ? chat : [];
    setChat([...base, { role: "user", text: question, at: now }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const a = await brain.ask(question, history, loadLivePrefs().userContext, true);
      setChat((c) => [...c, { role: "brain", text: a.answer, sources: a.sources, at: Date.now() }]);
    } catch (e) {
      const err = toCommandError(e);
      setError(err.kind === "not_signed_in" ? t("assistant.signIn") : err.message);
    } finally {
      setBusy(false);
      focus();
    }
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <main
      className="assistant"
      onMouseDown={startOverlayDrag}
      onKeyDown={(e) => {
        if (e.key === "Escape") hide();
      }}
    >
      <div className="assistant__head">
        <strong>✦ {t("assistant.title")}</strong>
        <span className="muted small">{t("assistant.hint")}</span>
        <span className="assistant__head-actions">
          {chat.length > 0 && (
            <button className="btn btn--small" onClick={() => setChat([])} disabled={busy}>
              {t("brain.ask.new")}
            </button>
          )}
          <button className="btn btn--small" aria-label={t("assistant.close")} onClick={hide}>
            ×
          </button>
        </span>
      </div>
      <div className="assistant__chat" data-no-drag>
        {chat.length === 0 && !busy && <p className="muted small">{t("assistant.examples")}</p>}
        {chat.map((m, i) => (
          <div key={i} className={`brain__msg brain__msg--${m.role}`}>
            <p className="brain__text">{m.text}</p>
            {m.role === "brain" && (
              <div className="assistant__meta small">
                {m.sources?.map((s) => (
                  <span key={s.id} className="pill pill--accent">
                    {s.title} · {formatDate(s.occurred_at, navigator.language)}
                  </span>
                ))}
                <button
                  className="link"
                  onClick={() =>
                    void navigator.clipboard?.writeText(m.text).then(() => {
                      setCopied(i);
                      setTimeout(() => setCopied(null), 1500);
                    })
                  }
                >
                  {copied === i ? t("tx.copied") : t("brain.copy")}
                </button>
              </div>
            )}
          </div>
        ))}
        {busy && <p className="muted small">{t("brain.thinking")}</p>}
        <div ref={end} />
      </div>
      {error && <p className="error-text small">{error}</p>}
      <textarea
        ref={input}
        className="assistant__input"
        rows={2}
        maxLength={2000}
        placeholder={t("assistant.placeholder")}
        aria-label={t("assistant.placeholder")}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
      />
    </main>
  );
}
