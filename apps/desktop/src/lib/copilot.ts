import type {
  AnswerCompletedPayload,
  AnswerDeltaPayload,
  AnswerTranslatedPayload,
  AnswerFailedPayload,
  AnswerStartedPayload,
  CopilotAnswer,
  CopilotMode,
  RealtimeEnvelope,
  TranscriptSegment,
} from "@meetpilot/shared-types";
import type { TranscriptState } from "./transcript";

export const MAX_ITEMS = 30;

/** "auto" = suggestion the server made by itself for a question someone asked. */
export type ItemMode = CopilotMode | "auto";

export interface CopilotItem {
  /** Manual requests: `m-<n>`; automatic suggestions: the server's question id. */
  id: string;
  mode: ItemMode;
  question: string | null;
  status: "pending" | "done" | "failed";
  answer?: CopilotAnswer;
  /** The answer as it is being written (automatic suggestions stream in). */
  partial?: string;
  error?: string;
}

export interface CopilotState {
  /** Newest first. */
  items: CopilotItem[];
}

export const emptyCopilot: CopilotState = { items: [] };

export type CopilotAction =
  | { kind: "pending"; id: string; mode: ItemMode; question: string | null }
  | { kind: "failed"; id: string; error: string; mode?: ItemMode; question?: string | null }
  | { kind: "answer"; id: string; mode: ItemMode; question: string | null; answer: CopilotAnswer }
  | { kind: "server"; envelope: RealtimeEnvelope }
  | { kind: "clear" };

export const manualId = (n: number): string => `m-${n}`;

export function copilotReducer(state: CopilotState, action: CopilotAction): CopilotState {
  switch (action.kind) {
    case "clear":
      return emptyCopilot;
    case "pending": {
      // Already waiting for this one (e.g. re-sent after a reconnect): keep it where it is.
      // Local requests ("m-…") are unique per window: a late pending after the answer is stale.
      if (state.items.some((i) => i.id === action.id && (i.status === "pending" || i.id.startsWith("m-")))) {
        return state;
      }
      // A new request always goes on top, even if an old finished card had the same id.
      const item: CopilotItem = { id: action.id, mode: action.mode, question: action.question, status: "pending" };
      const rest = state.items.filter((i) => i.id !== action.id);
      return { items: [item, ...rest].slice(0, MAX_ITEMS) };
    }
    case "failed": {
      if (!state.items.some((i) => i.id === action.id)) {
        // Failure without a pending item (e.g. quota reached before the request started).
        const item: CopilotItem = {
          id: action.id,
          mode: action.mode ?? "ask",
          question: action.question ?? null,
          status: "failed",
          error: action.error,
        };
        return { items: [item, ...state.items].slice(0, MAX_ITEMS) };
      }
      return { items: state.items.map((i) => (i.id === action.id ? { ...i, status: "failed", error: action.error } : i)) };
    }
    case "answer": {
      const done: CopilotItem = {
        id: action.id,
        mode: action.mode,
        question: action.question,
        status: "done",
        answer: action.answer,
      };
      const exists = state.items.some((i) => i.id === action.id);
      const items = exists ? state.items.map((i) => (i.id === action.id ? done : i)) : [done, ...state.items];
      return { items: items.slice(0, MAX_ITEMS) };
    }
    case "server": {
      const env = action.envelope;
      if (env.v !== 1) return state;
      switch (env.type) {
        case "answer.started": {
          const p = env.payload as AnswerStartedPayload;
          return copilotReducer(state, { kind: "pending", id: p.question_id, mode: modeOf(p.question_id), question: p.question });
        }
        case "answer.completed": {
          const { question_id, question, ...answer } = env.payload as AnswerCompletedPayload;
          return copilotReducer(state, { kind: "answer", id: question_id, mode: modeOf(question_id), question, answer });
        }
        case "answer.delta": {
          const p = env.payload as AnswerDeltaPayload;
          return {
            items: state.items.map((i) =>
              i.id === p.question_id && i.status === "pending" ? { ...i, partial: p.text } : i,
            ),
          };
        }
        case "answer.translated": {
          const p = env.payload as AnswerTranslatedPayload;
          return {
            items: state.items.map((i) =>
              i.id === p.question_id && i.answer
                ? { ...i, answer: { ...i.answer, answer_translation: p.answer_translation } }
                : i,
            ),
          };
        }
        case "answer.failed": {
          const p = env.payload as AnswerFailedPayload;
          return copilotReducer(state, {
            kind: "failed",
            id: p.question_id,
            error: p.message,
            mode: "auto",
            question: p.question ?? null,
          });
        }
        default:
          return state;
      }
    }
  }
}
export interface ContextSegment {
  source: TranscriptSegment["source"];
  text: string;
  start_ms: number;
}

/**
 * Context sent with a question: final segments plus live partials (the question someone
 * is still finishing is usually exactly what the user wants help with).
 */
export function contextFromTranscript(t: TranscriptState): ContextSegment[] {
  const segs: ContextSegment[] = t.finals.map((s) => ({ source: s.source, text: s.text, start_ms: s.start_ms }));
  for (const p of Object.values(t.partials)) {
    if (p && p.text.trim()) segs.push({ source: p.source, text: p.text, start_ms: p.start_ms });
  }
  return segs.sort((a, b) => a.start_ms - b.start_ms);
}

export function latestAnswer(state: CopilotState): CopilotItem | undefined {
  return state.items[0];
}

/** Answers the user asked for ("Responder ao que falaram", ids "r-…") vs. detected ones. */
function modeOf(questionId: string): CopilotItem["mode"] {
  return questionId.startsWith("r-") ? "reply" : "auto";
}
