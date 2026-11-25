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
  useEffect(() => {
    saveChat(chat);
  }, [chat]);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [chat, busy]);

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

  const status = busy ? t("assistant.status.thinking") : error ? t("assistant.status.error") : t("assistant.status.ready");
  return (
    <main
      className={`jarvis ${busy ? "jarvis--busy" : ""} ${error ? "jarvis--error" : ""}`}
      onMouseDown={startOverlayDrag}
      onKeyDown={(e) => {
        if (e.key === "Escape") hide();
      }}
    >
      <span className="jarvis__corner jarvis__corner--tl" aria-hidden="true" />
      <span className="jarvis__corner jarvis__corner--tr" aria-hidden="true" />
      <span className="jarvis__corner jarvis__corner--bl" aria-hidden="true" />
      <span className="jarvis__corner jarvis__corner--br" aria-hidden="true" />
      <header className="jarvis__head">
        <div className="jarvis__core" aria-hidden="true">
          <span className="jarvis__ring jarvis__ring--a" />
          <span className="jarvis__ring jarvis__ring--b" />
          <span className="jarvis__ring jarvis__ring--c" />
          <span className="jarvis__dot" />
        </div>
        <div className="jarvis__id">
          <span className="jarvis__name">MEETPILOT · {t("assistant.title")}</span>
          <span className="jarvis__status" role="status">
            <i className="jarvis__led" /> {status}
          </span>
        </div>
        <span className="jarvis__actions">
          {chat.length > 0 && (
            <button className="jarvis__btn" onClick={() => setChat([])} disabled={busy}>
              {t("brain.ask.new")}
            </button>
          )}
          <button className="jarvis__btn jarvis__btn--icon" aria-label={t("assistant.close")} onClick={hide}>
            ✕
          </button>
        </span>
      </header>
      <div className="jarvis__chat" data-no-drag>
        {chat.length === 0 && !busy && (
          <div className="jarvis__empty">
            <p className="jarvis__greet">{t("assistant.greeting")}</p>
            <p className="jarvis__examples">{t("assistant.examples")}</p>
          </div>
        )}
        {chat.map((m, i) => (
          <div key={i} className={`jarvis__msg jarvis__msg--${m.role}`}>
            <span className="jarvis__who">{m.role === "user" ? t("assistant.you") : "✦ MEETPILOT"}</span>
            <p className="jarvis__text">{m.text}</p>
            {m.role === "brain" && (
              <div className="jarvis__meta">
                {m.sources?.map((s) => (
                  <span key={s.id} className="jarvis__chip">
                    ◈ {s.title} · {formatDate(s.occurred_at, navigator.language)}
                  </span>
                ))}
                <button
                  className="jarvis__copy"
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
        {busy && (
          <div className="jarvis__thinking" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
            <span />
            <em>{t("assistant.status.thinking")}</em>
          </div>
        )}
        <div ref={end} />
      </div>
      {error && <p className="jarvis__error">⚠ {error}</p>}
      <div className="jarvis__prompt" data-no-drag>
        <span className="jarvis__caret" aria-hidden="true">
          ›
        </span>
        <textarea
          ref={input}
          className="jarvis__input"
          rows={2}
          maxLength={2000}
          placeholder={t("assistant.placeholder")}
          aria-label={t("assistant.placeholder")}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
        />
      </div>
      <footer className="jarvis__keys" aria-hidden="true">
        <span><kbd>↵</kbd> {t("assistant.key.send")}</span>
        <span><kbd>⇧↵</kbd> {t("assistant.key.newline")}</span>
        <span><kbd>esc</kbd> {t("assistant.key.close")}</span>
        <span className="jarvis__keys-right"><kbd>⌘⇧␣</kbd></span>
      </footer>
    </main>
  );
}
