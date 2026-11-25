import type { BrainSource, Turn } from "./brain";

export type AssistantMsg = Turn & { sources?: BrainSource[]; at: number };

const KEY = "meetpilot.assistant.chat";
/** Kept on this computer only, so the conversation survives closing the window. */
export const MAX_KEPT = 40;

export function loadChat(storage: Pick<Storage, "getItem"> = localStorage): AssistantMsg[] {
  try {
    const raw = JSON.parse(storage.getItem(KEY) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw
      .filter((m) => m && (m.role === "user" || m.role === "brain") && typeof m.text === "string")
      .slice(-MAX_KEPT);
  } catch {
    return [];
  }
}

export function saveChat(chat: AssistantMsg[], storage: Pick<Storage, "setItem"> = localStorage): void {
  try {
    storage.setItem(KEY, JSON.stringify(chat.slice(-MAX_KEPT)));
  } catch {
    // Storage may be full or unavailable; the conversation just isn't kept.
  }
}

/** A new conversation after a long pause, so old topics don't leak into new questions. */
export const FRESH_AFTER_MS = 2 * 60 * 60 * 1000;

export function historyFor(chat: AssistantMsg[], now: number): Turn[] {
  const last = chat[chat.length - 1];
  if (!last || now - last.at > FRESH_AFTER_MS) return [];
  return chat.map(({ role, text }) => ({ role, text }));
}
