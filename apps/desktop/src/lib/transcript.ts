import type {
  LinkStatus,
  RealtimeEnvelope,
  RealtimeErrorPayload,
  TranscriptSegment,
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
};

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
export function recentLines(state: TranscriptState, n: number): { text: string; source: string; final: boolean }[] {
  const lines = state.finals.map((s) => ({ text: s.text, source: s.source, final: true }));
  for (const p of Object.values(state.partials)) {
    if (p) lines.push({ text: p.text, source: p.source, final: false });
  }
  return lines.slice(-n);
}

export function formatClock(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function transcriptAsText(state: TranscriptState, labels: Record<string, string>): string {
  return state.finals.map((s) => `[${formatClock(s.start_ms)}] ${labels[s.source] ?? s.source}: ${s.text}`).join("\n");
}
