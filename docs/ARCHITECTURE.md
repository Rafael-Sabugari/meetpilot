# MeetPilot — Architecture

> Defining constraint: MeetPilot is a private desktop copilot. It captures audio that is
> already playing on the user's own computer, only after explicit user action. **It never
> joins a meeting as a bot, participant or attendee**, and never depends on meeting links,
> invitations, calendar access or meeting-provider APIs.

## 1. Overview

```text
┌──────────────────────── User's computer ─────────────────────────┐
│                                                                   │
│  Meeting app (Zoom/Meet/Teams/…) ──plays audio──► OS output       │
│                                                     │             │
│  ┌──────────── MeetPilot desktop (Tauri 2) ─────────┼──────────┐  │
│  │  Rust core                                       ▼          │  │
│  │  ┌─────────────────────────────┐   ┌─────────────────────┐  │  │
│  │  │ meetpilot-audio             │   │ OS capture backends │  │  │
│  │  │  CaptureSession (state m/c) │◄──│ WASAPI loopback     │  │  │
│  │  │  pause gate / reconnect     │   │ CoreAudio tap 14.6+ │  │  │
│  │  │  downmix → resample → PCM16 │   │ ScreenCaptureKit    │  │  │
│  │  │  20 ms frames + levels      │   │ Microphone (cpal)   │  │  │
│  │  └──────────┬──────────────────┘   └─────────────────────┘  │  │
│  │             │ events (status, level, error)                 │  │
│  │             ▼                                               │  │
│  │  React UI: main window + private always-on-top overlay      │  │
│  └─────────────┬───────────────────────────────────────────────┘  │
└────────────────┼──────────────────────────────────────────────────┘
                 │ (next slice) TLS WebSocket, authenticated, 16 kHz PCM frames
                 ▼
┌──────────────── MeetPilot backend (FastAPI modular monolith) ────┐
│ realtime gateway → STT adapter → context → question detection →  │
│ response generation / translation → events back to the overlay   │
│ auth · users · meetings · usage/billing · privacy · jobs          │
│ PostgreSQL (+pgvector) · Redis · S3-compatible storage           │
└──────────────────────────────────────────────────────────────────┘
```

## 2. Repository layout

| Path | What |
|---|---|
| `crates/audio-capture` | `meetpilot-audio`: capture abstraction, DSP, session, OS backends, `mp-audio-probe` CLI |
| `apps/desktop` | Tauri 2 app: React/TS UI (`src/`) and Rust shell (`src-tauri/`) |
| `packages/shared-types` | TypeScript contracts mirroring Rust/Python schemas |
| `backend` | FastAPI modular monolith (Python 3.12+, uv) |
| `docs` | Product, architecture, privacy, API, roadmap, audio-capture notes |
| `.github/workflows/ci.yml` | Lint/test on Linux; native build/test/bundle on Windows and macOS |

Monorepo with a Cargo workspace (root `Cargo.toml`) and npm workspaces (root `package.json`).

## 3. Desktop audio capture (`crates/audio-capture`)

### 3.1 Abstraction

```rust
trait AudioCaptureProvider: Send + Sync {
    fn backend_name(&self) -> String;
    fn capabilities(&self) -> PlatformCapabilities;      // OS, version, support, permissions, notes
    fn list_input_devices(&self) -> Result<Vec<DeviceInfo>>;
    fn list_output_devices(&self) -> Result<Vec<DeviceInfo>>;
    fn permission_status(&self, source: AudioSource) -> PermissionState;
    fn request_permission(&self, source: AudioSource) -> PermissionState;
    fn open(&self, source, selection, sink: RawAudioSink) -> Result<Box<dyn CaptureStream>>;
}
trait CaptureStream { fn native_format(); fn device_name(); fn pause(); fn resume(); } // Drop = stop
```

Start/pause/resume/stop/status/subscribe from the brief live in `CaptureSession`, which
works identically over every provider:

| Brief | Implementation |
|---|---|
| `start_system_audio_capture` / `start_microphone_capture` | `CaptureSession::start(&CaptureConfig { system_audio, microphone })` |
| `pause_capture` / `resume_capture` / `stop_capture` | `pause()` / `resume()` / `stop()` |
| `get_capture_status` | `status()` → `CaptureStatus` |
| `subscribe_to_audio_frames` | `EventHandler` receiving `SessionEvent::{Frame, Level, Status, Error}` |

### 3.2 Backends

| OS | System audio | Microphone | Notes |
|---|---|---|---|
| Windows 10/11 | WASAPI loopback (via `cpal` 0.18: input stream on a render endpoint sets `AUDCLNT_STREAMFLAGS_LOOPBACK`) | WASAPI (cpal) | Default device is followed on changes |
| macOS 14.6+ | CoreAudio process tap + private aggregate device (via `cpal` 0.18) | CoreAudio (cpal) | Needs "System Audio Recording" permission only |
| macOS 13.0–14.5 | ScreenCaptureKit audio stream (`objc2-screen-capture-kit`), 2×2 px/1 fps video discarded | CoreAudio (cpal) | Needs Screen & System Audio Recording permission |
| macOS < 13 | unsupported | CoreAudio | Clearly reported |
| Linux | not implemented (dev platform) | ALSA/PipeWire (cpal) | |

**Deviation from the brief, with rationale (rule 11):** the brief names ScreenCaptureKit
for macOS. On 14.6+ we prefer Apple's CoreAudio process-tap API because it captures
system audio *without* screen-recording permission (a smaller, more honest permission
for an audio product) and without a video stream. ScreenCaptureKit is still implemented
and used automatically on 13.0–14.5. `MEETPILOT_MACOS_SYSTEM_BACKEND=sck|tap` forces one.

For loopback we always open the output device with its current default format, so
MeetPilot never changes the user's speaker configuration.

### 3.3 Pipeline and threading

```text
OS callback thread ──try_send──► bounded queue (128 chunks) ──► worker thread (1 per source)
  (never blocks;                                                  owns the OS stream
   full ⇒ drop+count)                                             downmix → anti-alias LPF
                                                                  → linear resample → PCM16
                                                                  → 20 ms frames + levels
```

* Normalized format: **16 kHz, mono, PCM s16le, 20 ms (640-byte) frames**, tagged by source,
  with `seq`, `offset_ms`, and `gap_before` (set after pause, reconnect, drops, or format
  change — gaps are surfaced, never silently hidden).
* Resampler: 4th-order Butterworth (2 biquads, fc = 7.2 kHz) + linear interpolation; state
  carried across chunks (tests verify chunk-boundary invariance and > 35 dB alias rejection).
* **Pause guarantee:** `pause()` closes an atomic gate read by the audio callback, then
  synchronously waits for every worker to acknowledge; queued audio is discarded. After
  `pause()` returns no frame is emitted (tested). The OS stream is also paused where the
  backend supports it (cpal `pause`, ScreenCaptureKit stop).
* **Reconnect:** recoverable stream errors (device unplugged, endpoint invalidated, SCK stream
  stopped, OS resume failure) trigger up to 10 attempts at 1 s intervals with visible
  `Reconnecting { attempt }` status; user controls stay responsive during backoff.
* Errors raised on the real-time thread never block: if the queue is full they are parked in
  a slot the worker polls.
* System audio and microphone are kept as **separate tagged streams** (no mixing), so the
  backend can attribute "me" vs. "others" and apply echo handling (see roadmap) instead of
  sending a duplicated mix.

### 3.4 Desktop shell (`apps/desktop`)

* Tauri commands: `get_capabilities`, `list_devices`, `request_permission`, `start_capture`,
  `pause_capture`, `resume_capture`, `stop_capture`, `get_capture_status`,
  `set_overlay_protection`, `set_overlay_shown`.
* Events: `capture://status`, `capture://level` (~10 Hz/source), `capture://error`.
* Windows: `main` and `overlay` (always-on-top, undecorated, no taskbar entry, does not take
  focus, draggable, resizable). The overlay appears on start and hides on stop.
* Global shortcuts: Ctrl/⌘+Shift+M pause/resume, Ctrl/⌘+Shift+O show/hide overlay.
  Registration conflicts are logged and do not break the app.
* Closing the main window stops capture and exits — nothing records in the background.
* Overlay content protection (`set_content_protected`) is an opt-in, best-effort setting.

## 4. Backend (`backend/`)

FastAPI modular monolith; modules are packages under `app/` and are extracted only when
load, isolation or ownership requires it.

Implemented now: settings (`app/core/config.py`, secrets as `SecretStr`, production
guards), redacting JSON logging (`app/core/logging.py`), request-id middleware, health
endpoints, versioned realtime event schemas (`app/realtime/events.py`), STT provider
protocol (`app/providers/stt/base.py`).

Planned modules (see `ROADMAP.md`): `auth`, `users`, `meetings`, `transcripts`, `realtime`
gateway, `providers/{stt,llm,embeddings}`, `ai/{context,question_detection,response_generation,translation,summarization,action_items}`,
`knowledge`, `usage`, `billing`, `privacy`, `jobs`.

## 5. Contracts

* Rust ↔ UI: serde JSON (`snake_case` values, internally tagged enums), mirrored in
  `packages/shared-types/src/capture.ts`.
* Desktop ↔ backend realtime: versioned envelope `{v, type, meeting_id, seq, ts, payload}`;
  event names defined in both `packages/shared-types/src/realtime.ts` and
  `backend/app/realtime/events.py`, enforced by `backend/tests/test_event_contract.py`.

## 6. Key decisions

| Decision | Why |
|---|---|
| `cpal` for WASAPI loopback, mic, and macOS tap | Maintained, already implements loopback correctly on both OSes; less unsafe code to own |
| Own `objc2` ScreenCaptureKit backend | Needed for macOS 13–14.5; no SDK-specific build tooling |
| No resampling crate | Small, tested, dependency-free; speech-band quality is sufficient for STT |
| Worker thread owns the OS stream | No `Send` requirements on platform stream types; clean teardown |
| Separate sources, not a mix | Speaker attribution and duplicate avoidance downstream |
| Tauri 2 (not 3 alpha) | Brief requirement; 3.x is pre-release |
