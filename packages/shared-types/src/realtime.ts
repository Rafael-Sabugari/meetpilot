/**
 * Versioned realtime event contract (desktop <-> backend). The backend's
 * `app/realtime/events.py` defines the same names; `backend/tests/test_event_contract.py`
 * fails if they diverge.
 */

export const EVENT_SCHEMA_VERSION = 1 as const;

export const REALTIME_EVENT_TYPES = [
  "capture.started",
  "capture.paused",
  "capture.resumed",
  "capture.stopped",
  "transcript.partial",
  "transcript.final",
  "translation.updated",
  "question.detected",
  "answer.started",
  "answer.completed",
  "answer.failed",
  "usage.updated",
  "meeting.summary.ready",
  "error",
] as const;

export type RealtimeEventType = (typeof REALTIME_EVENT_TYPES)[number];

export interface RealtimeEnvelope<T = unknown> {
  /** Schema version of this envelope. */
  v: typeof EVENT_SCHEMA_VERSION;
  type: RealtimeEventType;
  /** Server-assigned meeting/session id. */
  meeting_id: string;
  /** Monotonic per-connection sequence number. */
  seq: number;
  /** ISO-8601 UTC timestamp. */
  ts: string;
  payload: T;
}

export type Language = "pt-BR" | "en" | "multi";

export interface TranscriptSegment {
  segment_id: string;
  source: "system" | "microphone";
  text: string;
  language: string | null;
  start_ms: number;
  end_ms: number;
  is_final: boolean;
  speaker: string | null;
}

export interface RealtimeErrorPayload {
  code: string;
  message: string;
  retryable: boolean;
}

export interface UsagePayload {
  session_seconds: number;
  remaining_seconds: number;
}

/** Desktop ↔ server link state (crates/realtime-client `LinkStatus`). */
export type LinkStatus =
  | { state: "connecting" }
  | { state: "connected" }
  | { state: "reconnecting"; attempt: number; retry_in_ms: number }
  | { state: "closed" }
  | { state: "failed"; reason: string; code: number | null };

/** Ask-Copilot (POST /api/v1/meetings/{id}/copilot). */
export type CopilotMode = "ask" | "reply" | "summarize";
export type CopilotTone = "natural" | "concise" | "technical" | "formal";
export type Grounding = "from_conversation" | "general_suggestion" | "needs_confirmation";

export interface CopilotAnswer {
  answer: string;
  grounding: Grounding;
  explanation: string;
  missing_info: string[];
  provider: string;
  requests_remaining: number;
}
