import { useCallback, useEffect, useRef, useState } from "react";
import type { MessageKey } from "../lib/i18n";
import { api, isTauri, toCommandError } from "../lib/api";
import {
  MAX_NOTE,
  addFact,
  brain,
  formatDate,
  inContext,
  noteFromFile,
  type BrainAnswer,
  type BrainSource,
  type Memory,
  type Turn,
} from "../lib/brain";

type T = (k: MessageKey) => string;
type Tab = "ask" | "briefing" | "memories" | "note";
const TABS: Tab[] = ["ask", "briefing", "memories", "note"];

/**
 * Second brain: ask about past meetings and notes, prepare a meeting, browse what was
 * remembered and add notes. Everything lives on the user's own MeetPilot server.
 */
export function BrainPanel(props: {
  t: T;
  locale: string;
  userContext: string | null;
  onUserContextChange: (next: string | null) => void;
  /** Changes when a meeting ends, so a new memory shows up. */
  refreshKey: number;
}) {
  const { t, locale, userContext, onUserContextChange, refreshKey } = props;
  const [tab, setTab] = useState<Tab>("ask");
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [query, setQuery] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    (q = query) =>
      brain
        .list(q)
        .then((m) => {
          setMemories(m);
          setError(null);
        })
        .catch((e) => setError(toCommandError(e).message)),
    [query],
  );
  useEffect(() => {
    void load();
  }, [refreshKey]);
  // While a meeting summary is being written, check again every few seconds.
  const processing = !!memories?.some((m) => m.status === "processing");
  useEffect(() => {
    if (!processing) return;
    const id = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(id);
  }, [processing, load]);

  const show = (id: string) => {
    setTab("memories");
    setQuery("");
    setOpenId(id);
    void load("");
  };

  return (
    <section className="card card--wide brain" data-testid="brain">
      <div className="row-between">
        <h2>{t("brain.title")}</h2>
        <span className="row">
          <span className="muted small">{memories ? `${memories.length} ${t("brain.count")}` : ""}</span>
          {isTauri() && (
            <button className="btn btn--small" onClick={() => void api.showAssistant().catch(() => {})} title={t("assistant.hint")}>
              ✦ {t("assistant.open")}
            </button>
          )}
        </span>
      </div>
      <p className="muted small">{t("brain.help")}</p>
      <div className="brain__tabs" role="tablist">
        {TABS.map((x) => (
          <button
            key={x}
            role="tab"
            aria-selected={tab === x}
            className={`btn btn--small ${tab === x ? "btn--primary" : ""}`}
            onClick={() => setTab(x)}
          >
            {t(`brain.tab.${x}` as MessageKey)}
          </button>
        ))}
      </div>
      {error && <p className="error-text">{error}</p>}
      {tab === "ask" && <AskBrain t={t} locale={locale} userContext={userContext} onSource={show} />}
      {tab === "briefing" && <Briefing t={t} locale={locale} userContext={userContext} onSource={show} />}
      {tab === "memories" && (
        <Memories
          t={t}
          locale={locale}
          memories={memories}
          query={query}
          onQuery={(q) => {
            setQuery(q);
            void load(q);
          }}
          openId={openId}
          onOpen={setOpenId}
          userContext={userContext}
          onUserContextChange={onUserContextChange}
          onChanged={() => void load()}
          onError={setError}
        />
      )}
      {tab === "note" && (
        <NewNote
          t={t}
          onSaved={(m) => {
            setTab("memories");
            setOpenId(m.id);
            setQuery("");
            void load("");
          }}
        />
      )}
      <p className="muted small">{t("brain.privacy")}</p>
    </section>
  );
}

function Sources(props: { t: T; locale: string; sources: BrainSource[]; onSource: (id: string) => void }) {
  const { t, locale, sources, onSource } = props;
  if (!sources.length) return null;
  return (
    <p className="brain__sources small">
      <span className="muted">{t("brain.sources")}: </span>
      {sources.map((s) => (
        <button key={s.id} className="pill pill--accent brain__source" onClick={() => onSource(s.id)}>
          {s.title} · {formatDate(s.occurred_at, locale)}
        </button>
      ))}
    </p>
  );
}

type Msg = Turn & { sources?: BrainSource[] };

function AskBrain(props: { t: T; locale: string; userContext: string | null; onSource: (id: string) => void }) {
  const { t, locale, userContext, onSource } = props;
  const [chat, setChat] = useState<Msg[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView?.({ block: "nearest" }), [chat, busy]);

  const send = async () => {
    const question = draft.trim();
    if (!question || busy) return;
    const history: Turn[] = chat.map(({ role, text }) => ({ role, text }));
    setChat((c) => [...c, { role: "user", text: question }]);
    setDraft("");
    setBusy(true);
    setError(null);
    try {
      const a: BrainAnswer = await brain.ask(question, history, userContext);
      setChat((c) => [...c, { role: "brain", text: a.answer, sources: a.sources }]);
    } catch (e) {
      setError(toCommandError(e).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="brain__ask">
      {chat.length === 0 && <p className="muted small">{t("brain.ask.examples")}</p>}
      <div className="brain__chat">
        {chat.map((m, i) => (
          <div key={i} className={`brain__msg brain__msg--${m.role}`}>
            <p className="brain__text">{m.text}</p>
            {m.sources && <Sources t={t} locale={locale} sources={m.sources} onSource={onSource} />}
          </div>
        ))}
        {busy && <p className="muted small">{t("brain.thinking")}</p>}
        <div ref={end} />
      </div>
      {error && <p className="error-text">{error}</p>}
      <form
        className="row ask"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <input
          aria-label={t("brain.ask.placeholder")}
          placeholder={t("brain.ask.placeholder")}
          value={draft}
          maxLength={2000}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="btn btn--primary" disabled={busy || !draft.trim()}>
          {t("brain.ask.send")}
        </button>
        {chat.length > 0 && (
          <button type="button" className="btn" onClick={() => setChat([])} disabled={busy}>
            {t("brain.ask.new")}
          </button>
        )}
      </form>
    </div>
  );
}

function Briefing(props: { t: T; locale: string; userContext: string | null; onSource: (id: string) => void }) {
  const { t, locale, userContext, onSource } = props;
  const [topic, setTopic] = useState("");
  const [result, setResult] = useState<BrainAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const run = async () => {
    if (!topic.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await brain.briefing(topic.trim(), userContext));
    } catch (e) {
      setError(toCommandError(e).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="brain__briefing">
      <p className="muted small">{t("brain.briefing.help")}</p>
      <form
        className="row ask"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <input
          aria-label={t("brain.briefing.placeholder")}
          placeholder={t("brain.briefing.placeholder")}
          value={topic}
          maxLength={1000}
          onChange={(e) => setTopic(e.target.value)}
        />
        <button className="btn btn--primary" disabled={busy || !topic.trim()}>
          {busy ? t("brain.thinking") : t("brain.briefing.run")}
        </button>
      </form>
      {error && <p className="error-text">{error}</p>}
      {result && (
        <div className="brain__msg brain__msg--brain">
          <p className="brain__text">{result.answer}</p>
          <Sources t={t} locale={locale} sources={result.sources} onSource={onSource} />
          <button
            className="btn btn--small"
            onClick={() => {
              void navigator.clipboard?.writeText(result.answer).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
          >
            {copied ? t("tx.copied") : t("brain.copy")}
          </button>
        </div>
      )}
    </div>
  );
}

function Memories(props: {
  t: T;
  locale: string;
  memories: Memory[] | null;
  query: string;
  onQuery: (q: string) => void;
  openId: string | null;
  onOpen: (id: string | null) => void;
  userContext: string | null;
  onUserContextChange: (next: string | null) => void;
  onChanged: () => void;
  onError: (e: string) => void;
}) {
  const { t, locale, memories, query, onQuery, openId, onOpen, userContext, onUserContextChange, onChanged, onError } = props;
  const [draft, setDraft] = useState(query);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  useEffect(() => setDraft(query), [query]);

  const remove = async (id: string) => {
    if (confirmDelete !== id) {
      setConfirmDelete(id);
      return;
    }
    setConfirmDelete(null);
    try {
      await brain.remove(id);
      onChanged();
    } catch (e) {
      onError(toCommandError(e).message);
    }
  };
  const dismiss = async (m: Memory, fact: string) => {
    try {
      await brain.update(m.id, { suggestions: m.suggestions.filter((s) => s !== fact) });
      onChanged();
    } catch (e) {
      onError(toCommandError(e).message);
    }
  };

  return (
    <div className="brain__memories">
      <form
        className="row ask"
        onSubmit={(e) => {
          e.preventDefault();
          onQuery(draft);
        }}
      >
        <input
          aria-label={t("brain.search")}
          placeholder={t("brain.search")}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="btn">{t("brain.search.run")}</button>
        {query && (
          <button type="button" className="btn" onClick={() => onQuery("")}>
            ×
          </button>
        )}
      </form>
      {memories === null && <p className="muted small">…</p>}
      {memories?.length === 0 && <p className="muted small">{query ? t("brain.none.search") : t("brain.none")}</p>}
      <ul className="brain__list">
        {memories?.map((m) => {
          const open = openId === m.id;
          return (
            <li key={m.id} className={`brain__item ${open ? "brain__item--open" : ""}`}>
              <button className="brain__head" onClick={() => onOpen(open ? null : m.id)} aria-expanded={open}>
                <span className={`pill ${m.kind === "meeting" ? "pill--accent" : "pill--muted"}`}>
                  {t(`brain.kind.${m.kind}` as MessageKey)}
                </span>
                <strong className="brain__title">{m.title}</strong>
                <span className="muted small">{formatDate(m.occurred_at, locale)}</span>
                {m.status === "processing" && <span className="pill pill--warn">{t("brain.processing")}</span>}
                {m.status === "failed" && <span className="pill pill--bad">{t("brain.failed")}</span>}
                {m.suggestions.some((s) => !inContext(userContext, s)) && (
                  <span className="pill pill--ok">{t("brain.hasSuggestions")}</span>
                )}
              </button>
              {open && (
                <div className="brain__body">
                  {m.status === "failed" && <p className="error-text small">{t("brain.failed.help")}</p>}
                  {m.summary && <p className="brain__text">{m.summary}</p>}
                  {m.details && <p className="brain__text brain__details">{m.details}</p>}
                  {m.suggestions.length > 0 && (
                    <div className="brain__suggestions">
                      <p className="muted small">{t("brain.suggestions")}</p>
                      {m.suggestions.map((s) => {
                        const added = inContext(userContext, s);
                        return (
                          <div key={s} className="brain__suggestion">
                            <span>{s}</span>
                            <button
                              className="btn btn--small"
                              disabled={added}
                              onClick={() => onUserContextChange(addFact(userContext, s) || null)}
                            >
                              {added ? t("brain.added") : t("brain.addToContext")}
                            </button>
                            <button className="link" aria-label={t("brain.dismiss")} onClick={() => void dismiss(m, s)}>
                              ×
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <div className="row">
                    <button className="btn btn--small btn--danger" onClick={() => void remove(m.id)}>
                      {confirmDelete === m.id ? t("brain.delete.confirm") : t("brain.delete")}
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function NewNote(props: { t: T; onSaved: (m: Memory) => void }) {
  const { t, onSaved } = props;
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!title.trim() || !text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await brain.addNote(title.trim(), text.trim()));
      setTitle("");
      setText("");
    } catch (e) {
      setError(toCommandError(e).message);
    } finally {
      setBusy(false);
    }
  };
  const importFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 2_000_000) {
      setError(t("brain.note.tooBig"));
      return;
    }
    const note = noteFromFile(file.name, await file.text());
    setTitle(note.title);
    setText(note.text);
  };
  return (
    <div className="brain__note form">
      <p className="muted small">{t("brain.note.help")}</p>
      <label>
        <span className="muted small">{t("brain.note.title")}</span>
        <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        <span className="muted small">
          {t("brain.note.text")} ({text.length}/{MAX_NOTE})
        </span>
        <textarea className="ctx-input" rows={8} maxLength={MAX_NOTE} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="row-between">
        <label className="btn btn--small brain__file">
          {t("brain.note.import")}
          <input
            type="file"
            accept=".txt,.md,.markdown,.csv,.json,.html,text/*"
            hidden
            onChange={(e) => void importFile(e.target.files?.[0])}
          />
        </label>
        <button className="btn btn--primary" disabled={busy || !title.trim() || !text.trim()} onClick={() => void save()}>
          {t("brain.note.save")}
        </button>
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
