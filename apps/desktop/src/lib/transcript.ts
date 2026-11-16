import type {
  LinkStatus,
  RealtimeEnvelope,
  QuestionDetectedPayload,
  RealtimeErrorPayload,
  TranscriptSegment,
  TranslationPayload,
  UsagePayload,
} from "@meetpilot/shared-types";

export const MAX_FINAL_SEGMENTS = 300;

export interface TranscriptState {
  finals: TranscriptSegment[];
  /** Latest interim text per source; cleared when that source's final arrives. */
  partials: Partial<Record<TranscriptSegment["source"], TranscriptSegment>>;
  link: LinkStatus | null;
  usage: UsagePayload | null;
  remainingAtStart: number | null;
  provider: string | null;
  errors: RealtimeErrorPayload[];
  lastSeq: number;
  /** Translation per final segment id (bounded like `finals`). */
  translations: Record<string, string>;
  /** Segment ids of lines detected as questions to answer. */
  questions: string[];
}

export const emptyTranscript: TranscriptState = {
  finals: [],
  partials: {},
  link: null,
  usage: null,
  remainingAtStart: null,
  provider: null,
  errors: [],
  lastSeq: -1,
  translations: {},
  questions: [],
};

/** Equal except for case, accents, punctuation and spacing. */
export function sameText(a: string, b: string): boolean {
  const n = (s: string) =>
    s
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim();
  return n(a) === n(b);
}

function bounded<T>(rec: Record<string, T>, key: string, value: T): Record<string, T> {
  const next = { ...rec, [key]: value };
  const keys = Object.keys(next);
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_FINAL_SEGMENTS))) delete next[k];
  return next;
}

export type TranscriptAction =
  | { kind: "server"; envelope: RealtimeEnvelope }
  | { kind: "link"; status: LinkStatus }
  | { kind: "reset" };

export function transcriptReducer(state: TranscriptState, action: TranscriptAction): TranscriptState {
  switch (action.kind) {
    case "reset":
      return emptyTranscript;
    case "link":
      // "connecting" is only emitted when a new capture starts (reconnects emit
      // "reconnecting"), so it is the one safe point to clear the previous transcript.
      if (action.status.state === "connecting") return { ...emptyTranscript, link: action.status };
      // Interim text can never become final once the link is gone: drop it.
      if (action.status.state === "closed" || action.status.state === "failed")
        return { ...state, partials: {}, link: action.status };
      return { ...state, link: action.status };
    case "server": {
      const env = action.envelope;
      if (env.v !== 1) return state; // unknown schema version: ignore safely
      const next = { ...state, lastSeq: Math.max(state.lastSeq, env.seq) };
      switch (env.type) {
        case "capture.started": {
          const p = env.payload as { provider?: string; remaining_seconds?: number };
          return { ...next, provider: p.provider ?? null, remainingAtStart: p.remaining_seconds ?? null };
        }
        case "transcript.partial": {
          const seg = env.payload as TranscriptSegment;
          return { ...next, partials: { ...next.partials, [seg.source]: seg } };
        }
        case "transcript.final": {
          const seg = env.payload as TranscriptSegment;
          if (!seg.text.trim()) return next;
          const partials = { ...next.partials };
          delete partials[seg.source];
          const finals = [...next.finals, seg].slice(-MAX_FINAL_SEGMENTS);
          return { ...next, finals, partials };
        }
        case "translation.updated": {
          const p = env.payload as TranslationPayload;
          if (!p.text?.trim()) return next;
          // A "translation" equal to the original (already in that language) adds nothing.
          const original = next.finals.find((f) => f.segment_id === p.segment_id)?.text;
          if (original !== undefined && sameText(original, p.text)) return next;
          return { ...next, translations: bounded(next.translations, p.segment_id, p.text) };
        }
        case "question.detected": {
          const p = env.payload as QuestionDetectedPayload;
          const ids = (p.segment_ids?.length ? p.segment_ids : [p.segment_id]).filter((id) => !next.questions.includes(id));
          if (!ids.length) return next;
          return { ...next, questions: [...next.questions, ...ids].slice(-MAX_FINAL_SEGMENTS) };
        }
        case "usage.updated":
          return { ...next, usage: env.payload as UsagePayload };
        case "error":
          return { ...next, errors: [...next.errors, env.payload as RealtimeErrorPayload].slice(-5) };
        default:
          return next;
      }
    }
  }
}

/** Last `n` lines for the compact overlay, oldest first, with live partials appended. */
export function recentLines(
  state: TranscriptState,
  n: number,
): { text: string; source: string; final: boolean; translation?: string }[] {
  const lines: { text: string; source: string; final: boolean; translation?: string }[] = state.finals.map((s) => ({
    text: s.text,
    source: s.source,
    final: true,
    translation: state.translations[s.segment_id],
  }));
  for (const p of Object.values(state.partials)) {
    if (p) lines.push({ text: p.text, source: p.source, final: false });
  }
  return lines.slice(-n);
}

/** How translated lines are shown. */
export type TranslationView = "both" | "translation" | "original";

export function formatClock(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function transcriptAsText(state: TranscriptState, labels: Record<string, string>): string {
  return state.finals
    .map((s) => {
      const line = `[${formatClock(s.start_ms)}] ${labels[s.source] ?? s.source}: ${s.text}`;
      const tr = state.translations[s.segment_id];
      return tr ? `${line}\n        → ${tr}` : line;
    })
    .join("\n");
}
