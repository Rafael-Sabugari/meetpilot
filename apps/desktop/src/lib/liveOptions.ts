import type { AutoSuggestions, CopilotTone, LiveOptions, TranslateTo } from "@meetpilot/shared-types";
import type { TranslationView } from "./transcript";

export interface LivePrefs extends LiveOptions {
  translationView: TranslationView;
}

export const MAX_USER_CONTEXT = 20000;

export const DEFAULT_LIVE: LivePrefs = {
  autoSuggestions: "directed",
  tone: "natural",
  translateTo: null,
  userContext: null,
  translationView: "both",
};

const KEY = "meetpilot.live.v1";
const AUTO: AutoSuggestions[] = ["off", "directed", "all"];
const TONES: CopilotTone[] = ["natural", "concise", "technical", "formal"];
const TARGETS: TranslateTo[] = ["pt-BR", "en"];
const VIEWS: TranslationView[] = ["both", "translation", "original"];

/** Parse stored preferences, falling back field by field to safe defaults. */
export function parseLivePrefs(raw: string | null): LivePrefs {
  let v: Partial<Record<keyof LivePrefs, unknown>> = {};
  try {
    v = raw ? (JSON.parse(raw) as typeof v) : {};
  } catch {
    v = {};
  }
  const pick = <T>(x: unknown, allowed: readonly T[], d: T): T => (allowed.includes(x as T) ? (x as T) : d);
  return {
    autoSuggestions: pick(v.autoSuggestions, AUTO, DEFAULT_LIVE.autoSuggestions),
    tone: pick(v.tone, TONES, DEFAULT_LIVE.tone),
    translateTo: pick<TranslateTo | null>(v.translateTo, [...TARGETS, null], null),
    userContext:
      typeof v.userContext === "string" && v.userContext.trim()
        ? v.userContext.slice(0, MAX_USER_CONTEXT)
        : null,
    translationView: pick(v.translationView, VIEWS, DEFAULT_LIVE.translationView),
  };
}

export function loadLivePrefs(): LivePrefs {
  try {
    return parseLivePrefs(localStorage.getItem(KEY));
  } catch {
    return DEFAULT_LIVE;
  }
}

export function saveLivePrefs(p: LivePrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // Preferences are a convenience; ignore storage failures.
  }
}

/** What the server needs (the view mode is local only). */
export function toLiveOptions(p: LivePrefs): LiveOptions {
  return {
    autoSuggestions: p.autoSuggestions,
    tone: p.tone,
    translateTo: p.translateTo,
    userContext: p.userContext?.trim() ? p.userContext.trim().slice(0, MAX_USER_CONTEXT) : null,
  };
}
