/**
 * Local capture contracts. Mirrors `crates/audio-capture` serde output exactly
 * (snake_case values, `state`/`kind`/`status` tags). Keep both sides in sync.
 */

export type AudioSource = "system" | "microphone";

export type PermissionState =
  | "granted"
  | "denied"
  | "not_determined"
  | "not_required"
  | "unknown";

export type SupportLevel =
  | { status: "supported"; backend: string }
  | { status: "unsupported"; reason: string };

export interface PlatformCapabilities {
  os: string;
  os_version: string | null;
  system_audio: SupportLevel;
  microphone: SupportLevel;
  system_audio_permission: PermissionState;
  microphone_permission: PermissionState;
  notes: string[];
}

export interface DeviceInfo {
  id: string;
  name: string;
  direction: "input" | "output";
  is_default: boolean;
}

export interface NativeFormat {
  sample_rate: number;
  channels: number;
}

export interface SourceSelection {
  /** null = OS default device. */
  device_id: string | null;
}

export type CaptureError =
  | { kind: "permission_denied"; source: AudioSource; hint: string }
  | { kind: "device_not_found"; source: AudioSource }
  | { kind: "device_unavailable"; source: AudioSource; detail: string }
  | { kind: "unsupported"; source: AudioSource; reason: string }
  | { kind: "backend"; source: AudioSource; detail: string }
  | { kind: "invalid_config"; detail: string }
  | { kind: "invalid_state"; action: string; state: string };

export type SourceStateTag =
  | { state: "starting" }
  | { state: "capturing" }
  | { state: "paused" }
  | { state: "reconnecting"; attempt: number }
  | { state: "failed"; error: CaptureError }
  | { state: "stopped" };

export type SourceStatus = SourceStateTag & {
  source: AudioSource;
  device_name: string | null;
  native_format: NativeFormat | null;
  frames_emitted: number;
  captured_ms: number;
  dropped_chunks: number;
  reconnects: number;
};

export type SessionState = "idle" | "capturing" | "paused";

export interface CaptureStatus {
  state: SessionState;
  sources: SourceStatus[];
}

export interface LevelReading {
  source: AudioSource;
  rms_dbfs: number;
  peak: number;
}

/** Internal PCM format sent to speech-to-text. */
export const PCM_FORMAT = {
  sampleRate: 16000,
  channels: 1,
  encoding: "pcm_s16le",
  frameMs: 20,
} as const;
