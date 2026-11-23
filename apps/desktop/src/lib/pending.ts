import type { TranscriptState } from "./transcript";

/** What Others said after the last line already sent to the copilot ("Responder ao que falaram"). */
export interface PendingSpeech {
  text: string;
  /** Translation of the finished lines, when live translation is on ("" otherwise). */
  translation: string;
  /** Lines (finals, plus the line still being spoken). */
  count: number;
  /** Segment id to remember as sent, so the next send starts after it. */
  lastId: string | null;
}

const MAX_CHARS = 3_800;

export function pendingFromOthers(state: TranscriptState, sentUpTo: string | null): PendingSpeech {
  const finals = state.finals.filter((s) => s.source === "system");
  let start = 0;
  if (sentUpTo) {
    const i = finals.findIndex((s) => s.segment_id === sentUpTo);
    start = i >= 0 ? i + 1 : 0;
  }
  const unsent = finals.slice(start);
  const parts = unsent.map((s) => s.text.trim()).filter(Boolean);
  const translation = unsent
    .map((s) => state.translations[s.segment_id]?.trim())
    .filter(Boolean)
    .join(" ");
  const live = state.partials.system?.text.trim();
  if (live) parts.push(live);
  let text = parts.join(" ");
  // Keep the most recent part if it is very long.
  if (text.length > MAX_CHARS) text = "…" + text.slice(text.length - MAX_CHARS);
  return {
    text,
    translation,
    count: parts.length,
    lastId: finals.length ? finals[finals.length - 1].segment_id : sentUpTo,
  };
}
