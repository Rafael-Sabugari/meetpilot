import { api } from "./api";
import { MAX_USER_CONTEXT } from "./liveOptions";

export interface Memory {
  id: string;
  kind: "meeting" | "note";
  status: "processing" | "ready" | "failed";
  title: string;
  occurred_at: string;
  meeting_id: string | null;
  summary: string;
  details: string;
  /** Facts about you said in the meeting, worth adding to "My context". */
  suggestions: string[];
}

export interface BrainSource {
  id: string;
  title: string;
  kind: string;
  occurred_at: string;
}

export interface BrainAnswer {
  answer: string;
  sources: BrainSource[];
  input_tokens: number;
  output_tokens: number;
}

export interface Turn {
  role: "user" | "brain";
  text: string;
}

export const MAX_NOTE = 60000;

/** Query-string value with only the characters the app lets through. */
export function encodeQuery(v: string): string {
  return encodeURIComponent(v).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
}

export const brain = {
  list: (q?: string) =>
    api.brain<Memory[]>("GET", q?.trim() ? `brain/memories?limit=200&q=${encodeQuery(q.trim())}` : "brain/memories?limit=200"),
  addNote: (title: string, text: string) => api.brain<Memory>("POST", "brain/memories", { title, text }),
  update: (id: string, patch: Partial<Pick<Memory, "title" | "summary" | "details" | "suggestions">>) =>
    api.brain<Memory>("PATCH", `brain/memories/${encodeQuery(id)}`, patch),
  remove: (id: string) => api.brain<null>("DELETE", `brain/memories/${encodeQuery(id)}`),
  ask: (question: string, history: Turn[], userContext: string | null, assistant = false) =>
    api.brain<BrainAnswer>("POST", "brain/ask", {
      question,
      history: history.slice(-12).map((t) => ({ role: t.role, text: t.text.slice(0, 8000) })),
      user_context: userContext,
      assistant,
    }),
  briefing: (topic: string, userContext: string | null) =>
    api.brain<BrainAnswer>("POST", "brain/briefing", { topic, user_context: userContext }),
};

const fold = (s: string) =>
  s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();

export function inContext(context: string | null, fact: string): boolean {
  return !!context && fold(context).includes(fold(fact));
}

/** "My context" with the fact added as a new line (unchanged if already there or full). */
export function addFact(context: string | null, fact: string): string {
  const base = (context ?? "").trimEnd();
  const clean = fact.trim();
  if (!clean || inContext(base, clean)) return base;
  const next = base ? `${base}\n- ${clean}` : `- ${clean}`;
  return next.length > MAX_USER_CONTEXT ? base : next;
}

/** A text file imported as a note: its name (without extension) and its text. */
export function noteFromFile(name: string, text: string): { title: string; text: string } {
  const title = name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim().slice(0, 200) || "Nota";
  return { title, text: text.slice(0, MAX_NOTE) };
}

export function formatDate(iso: string, locale: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: "numeric" });
}
