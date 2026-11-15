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
