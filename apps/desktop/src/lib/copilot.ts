import type { CopilotAnswer, CopilotMode, TranscriptSegment } from "@meetpilot/shared-types";
import type { TranscriptState } from "./transcript";

export const MAX_ITEMS = 30;

export interface CopilotItem {
  id: number;
  mode: CopilotMode;
  question: string | null;
  status: "pending" | "done" | "failed";
  answer?: CopilotAnswer;
  error?: string;
}

export interface CopilotState {
  /** Newest first. */
  items: CopilotItem[];
}

export const emptyCopilot: CopilotState = { items: [] };

export type CopilotAction =
  | { kind: "pending"; id: number; mode: CopilotMode; question: string | null }
  | { kind: "failed"; id: number; error: string }
  | { kind: "answer"; id: number; mode: CopilotMode; question: string | null; answer: CopilotAnswer }
  | { kind: "clear" };

export function copilotReducer(state: CopilotState, action: CopilotAction): CopilotState {
  switch (action.kind) {
    case "clear":
      return emptyCopilot;
    case "pending": {
      if (state.items.some((i) => i.id === action.id)) return state;
      const item: CopilotItem = { id: action.id, mode: action.mode, question: action.question, status: "pending" };
      return { items: [item, ...state.items].slice(0, MAX_ITEMS) };
    }
    case "failed":
      return { items: state.items.map((i) => (i.id === action.id ? { ...i, status: "failed", error: action.error } : i)) };
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
