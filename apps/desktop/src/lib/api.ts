import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
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

export type StatusDto = CaptureStatus & { demo: boolean };

export interface CommandError {
  kind: string;
  message: string;
  detail?: CaptureError;
}

export interface StartRequest {
  systemAudio: SourceSelection | null;
  microphone: SourceSelection | null;
  demo: boolean;
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
};

export const events = {
  onStatus: (cb: (s: CaptureStatus) => void): Promise<UnlistenFn> =>
    listen<CaptureStatus>("capture://status", (e) => cb(e.payload)),
  onLevel: (cb: (l: LevelReading) => void): Promise<UnlistenFn> =>
    listen<LevelReading>("capture://level", (e) => cb(e.payload)),
  onError: (cb: (e: CaptureError) => void): Promise<UnlistenFn> =>
    listen<CaptureError>("capture://error", (e) => cb(e.payload)),
};
