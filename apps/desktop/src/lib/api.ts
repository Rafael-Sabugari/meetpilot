import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  CopilotAnswer,
  CopilotMode,
  CopilotTone,
  Language,
  LinkStatus,
  RealtimeEnvelope,
  AudioSource,
  CaptureError,
  CaptureStatus,
  DeviceInfo,
  LevelReading,
  PermissionState,
  PlatformCapabilities,
  SourceSelection,
} from "@meetpilot/shared-types";

export interface CapabilitiesDto {
  backend: string | null;
  capabilities: PlatformCapabilities | null;
  demo_available: boolean;
}

export interface DevicesDto {
  inputs: DeviceInfo[];
  outputs: DeviceInfo[];
}

export type StatusDto = CaptureStatus & {
  demo: boolean;
  meeting_id?: string | null;
  transcribing?: boolean;
  dropped_frames?: number;
};

export interface AccountInfo {
  email: string;
  plan: string;
  server: string;
}

export interface UsageDto {
  used_seconds: number;
  limit_seconds: number;
  remaining_seconds: number;
}

export interface AskRequest {
  mode: CopilotMode;
  question?: string | null;
  transcript: { source: "system" | "microphone"; text: string; start_ms: number }[];
  tone: CopilotTone;
  language?: "pt-BR" | "en" | null;
}

export type AnswerEvent = CopilotAnswer & { id: number; mode: CopilotMode; question: string | null };

export interface PendingEvent {
  id: number;
  mode: CopilotMode;
  question: string | null;
  state: "pending" | "failed";
  error?: string;
}

export interface Credentials {
  server: string;
  email: string;
  password: string;
}

export interface CommandError {
  kind: string;
  message: string;
  detail?: CaptureError;
}

export interface StartRequest {
  systemAudio: SourceSelection | null;
  microphone: SourceSelection | null;
  demo: boolean;
  /** Present only when the user enabled live transcription (sends audio to the server). */
  transcription?: { language: Language } | null;
}

export const isTauri = (): boolean =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export function toCommandError(e: unknown): CommandError {
  if (e && typeof e === "object" && "message" in e && "kind" in e) return e as CommandError;
  return { kind: "unknown", message: String(e) };
}

export const api = {
  capabilities: () => invoke<CapabilitiesDto>("get_capabilities"),
  devices: () => invoke<DevicesDto>("list_devices"),
  requestPermission: (source: AudioSource) =>
    invoke<PermissionState>("request_permission", { source }),
  start: (request: StartRequest) => invoke<StatusDto>("start_capture", { request }),
  pause: () => invoke<StatusDto>("pause_capture"),
  resume: () => invoke<StatusDto>("resume_capture"),
  stop: () => invoke<StatusDto>("stop_capture"),
  status: () => invoke<StatusDto>("get_capture_status"),
  setOverlayProtection: (enabled: boolean) =>
    invoke<boolean>("set_overlay_protection", { enabled }),
  setOverlayShown: (visible: boolean) => invoke<void>("set_overlay_shown", { visible }),
  login: (credentials: Credentials) => invoke<AccountInfo>("account_login", { credentials }),
  register: (credentials: Credentials) => invoke<AccountInfo>("account_register", { credentials }),
  logout: () => invoke<void>("account_logout"),
  account: () => invoke<AccountInfo | null>("account_status"),
  usage: () => invoke<UsageDto>("account_usage"),
  askCopilot: (request: AskRequest) => invoke<AnswerEvent>("ask_copilot", { request }),
};

export const events = {
  onStatus: (cb: (s: CaptureStatus) => void): Promise<UnlistenFn> =>
    listen<CaptureStatus>("capture://status", (e) => cb(e.payload)),
  onLevel: (cb: (l: LevelReading) => void): Promise<UnlistenFn> =>
    listen<LevelReading>("capture://level", (e) => cb(e.payload)),
  onError: (cb: (e: CaptureError) => void): Promise<UnlistenFn> =>
    listen<CaptureError>("capture://error", (e) => cb(e.payload)),
  onLink: (cb: (s: LinkStatus) => void): Promise<UnlistenFn> =>
    listen<LinkStatus>("realtime://status", (e) => cb(e.payload)),
  onServer: (cb: (env: RealtimeEnvelope) => void): Promise<UnlistenFn> =>
    listen<RealtimeEnvelope>("realtime://event", (e) => cb(e.payload)),
  onCopilotAnswer: (cb: (a: AnswerEvent) => void): Promise<UnlistenFn> =>
    listen<AnswerEvent>("copilot://answer", (e) => cb(e.payload)),
  onCopilotPending: (cb: (p: PendingEvent) => void): Promise<UnlistenFn> =>
    listen<PendingEvent>("copilot://pending", (e) => cb(e.payload)),
};
