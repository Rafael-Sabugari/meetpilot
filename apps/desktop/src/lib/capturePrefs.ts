/** Capture options remembered between launches (this computer only). */
export type MeetingLanguage = "pt-BR" | "en" | "multi";

export interface CapturePrefs {
  useSystem: boolean;
  useMic: boolean;
  /** Device name; "" = system default. */
  systemDevice: string;
  micDevice: string;
  transcribe: boolean;
  language: MeetingLanguage;
  protectOverlay: boolean;
  /** "cloud" = Deepgram; "local" = Whisper on this computer (free). */
  engine: "cloud" | "local";
  /** Whisper model id; "" = the recommended one. */
  localModel: string;
  /** Free mode: label who is speaking among the others ("Pessoa 1", "Pessoa 2"). */
  speakers: boolean;
}

const KEY = "meetpilot.capture.v1";
const LANGS: MeetingLanguage[] = ["pt-BR", "en", "multi"];

export function defaultCapturePrefs(browserLanguage = "en"): CapturePrefs {
  return {
    useSystem: true,
    useMic: false,
    systemDevice: "",
    micDevice: "",
    transcribe: false,
    language: browserLanguage.toLowerCase().startsWith("pt") ? "pt-BR" : "en",
    protectOverlay: false,
    engine: "cloud",
    localModel: "",
    speakers: false,
  };
}

export function parseCapturePrefs(raw: string | null, browserLanguage = "en"): CapturePrefs {
  const d = defaultCapturePrefs(browserLanguage);
  let v: Record<string, unknown> = {};
  try {
    v = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
  } catch {
    v = {};
  }
  const bool = (x: unknown, def: boolean) => (typeof x === "boolean" ? x : def);
  const str = (x: unknown) => (typeof x === "string" ? x.slice(0, 200) : "");
  return {
    useSystem: bool(v.useSystem, d.useSystem),
    useMic: bool(v.useMic, d.useMic),
    systemDevice: str(v.systemDevice),
    micDevice: str(v.micDevice),
    transcribe: bool(v.transcribe, d.transcribe),
    language: LANGS.includes(v.language as MeetingLanguage) ? (v.language as MeetingLanguage) : d.language,
    protectOverlay: bool(v.protectOverlay, d.protectOverlay),
    engine: v.engine === "local" ? "local" : "cloud",
    localModel: typeof v.localModel === "string" ? v.localModel.slice(0, 64) : "",
    speakers: bool(v.speakers, d.speakers),
  };
}

export function loadCapturePrefs(): CapturePrefs {
  try {
    return parseCapturePrefs(localStorage.getItem(KEY), navigator.language ?? "en");
  } catch {
    return defaultCapturePrefs();
  }
}

export function saveCapturePrefs(p: CapturePrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    // Convenience only.
  }
}

/** A remembered device that is not connected now falls back to the system default. */
export function availableDevice(saved: string, names: string[]): string {
  return saved && names.includes(saved) ? saved : "";
}
