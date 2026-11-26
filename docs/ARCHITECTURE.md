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
                 │ TLS WebSocket, stream-token auth, 16 kHz PCM frames (crates/realtime-client)
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
| `crates/realtime-client` | `meetpilot-realtime`: REST client (auth, meetings) and resilient audio uplink |
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

## 3.5 Live transcription path (slice 2)

```text
CaptureSession ─Frame(seq)─► StreamHandle.send_frame (try_send, never blocks)
                               │ bounded queue 1500 frames (30 s) — overflow counted, shown in UI
                               ▼
                      uplink task (tokio) ──WS──► gateway ──► STT session per source ──► Deepgram
                       • auth with stream token        • dedup by seq (persisted)    (nova-3,
                       • reconnect w/ backoff,         • metering in integer frames   mip_opt_out)
                         fresh token each time         • pause → KeepAlive only
                       • replay last 250 frames        • quota / concurrency checks
                       • stop() waits for finals ◄──── transcript events (meeting-relative ms)
```

* The desktop never holds provider keys. It holds a short-lived access token and a refresh
  token **in memory only** (re-login after restart; OS keychain planned).
* Start order: meeting + stream are created **before** local capture starts, so no audio is
  lost at the beginning; pause gates audio locally first, then notifies the server.
* `stop` stops local capture immediately; the uplink then flushes queued frames, sends
  `capture.stopped`, waits (≤10 s) for final segments, and the meeting is marked finished.

## 3.6 Ask Copilot (slice 3)

```text
UI transcript (finals + live partials) ──► ask_copilot (Tauri) ──► POST /meetings/{id}/copilot
                                                                   │ context budget (12k chars, newest first)
                                                                   │ prompt rules + JSON schema
                                                                   ▼
                                                     LLMProvider (OpenAI Responses, store=false)
answer + grounding ◄── copilot://answer (main window and overlay) ◄─┘ validate (placeholders ⇒ needs_confirmation)
```

* **Context comes from the desktop**, which already has the transcript. The server stays
  stateless (survives restarts, scales horizontally) and stores nothing; it enforces the
  size budget itself. The same endpoint would serve a future on-device transcription mode.
* Structured output (strict JSON schema) guarantees every answer carries a grounding label:
  *From the conversation*, *General suggestion*, *Needs confirmation*. The brief's *From your
  profile/documents* labels arrive with the knowledge base (P1).
* Rules in the prompt (`app/ai/copilot.py`): answer language/tone, concise, never invent the
  user's experience or credentials (use `[placeholders]` + `needs_confirmation`), never
  attribute statements not in the transcript, treat the transcript as untrusted (prompt
  injection), no help deceiving participants. A server-side check forces
  `needs_confirmation` whenever placeholders appear.
* Providers: `anthropic` (Claude Messages API; structured output by forcing one tool whose
  `input_schema` is the JSON schema; default `claude-haiku-5-5`), `openai` (Responses API,
  strict JSON schema), `fake` (dev/tests). Same protocol, chosen by `MEETPILOT_LLM_PROVIDER`.
* Cost controls: monthly request quota per plan, per-minute rate limit, bounded context and
  output tokens; tokens metered per request.

## 3.7 Live assistance (slice 4)

```text
STT final line ─► LiveAssist (per stream, in memory)
                   ├─ rolling context (last 400 lines per meeting, RAM only; dropped on finish/delete)
                   ├─ question detector (app/ai/questions.py, heuristic PT/EN, no LLM call)
                   │    └─ AutoAnswerPolicy (one in flight, debounce, dedupe, per-minute cap)
                   │         └─ copilot.ask(...) ─► answer.started / completed / failed
                   └─ translation queue (finals only, bounded) ─► LLM ─► translation.updated
desktop: copilot.settings ─► LiveAssist.update_settings   (re-sent by the client after reconnects)
```

* **Detection is two-stage.** A free heuristic (trailing "?" or a request opener, plus
  second-person cues for "directed") settles clear lines. Lines it cannot settle but that
  carry interrogative cues (STT dropped the "?", no "você"/"you", a question whose target is
  unclear) get a small LLM check (`app/ai/question_check.py`) with the last 8 lines and the
  user's context (e.g. their name). Statements and greetings never reach the LLM.
* **User context ("Meu contexto")**: free text the user writes about themselves and the
  meeting, stored only on the desktop (localStorage), sent in `copilot.settings` and with
  manual questions, held in server memory for the meeting only. The prompt treats it as true
  facts about the user; answers based on it are labelled `from_user_context`. Only the
  *others* stream (system audio) is checked: the user's own questions are not answered.
* Auto answers reuse the Ask-Copilot prompt rules and grounding enforcement; they share the
  monthly copilot quota with manual questions and are metered the same way
  (`copilot_request`, tokens). Translations are metered as `translation_request`.
* Question ids derive from the segment id (`q-<source>-<start_ms>`), which stays unique across
  reconnects, so a deferred answer always matches the detection the overlay showed.
* The server keeps the live context only in memory for the meeting's duration; manual
  Ask-Copilot requests still send their own context from the desktop (server stays
  stateless for them).

## 3.8 Free transcription mode (Whisper on the user's computer)

```text
mic/system audio ─► LocalTranscriber (crates/local-stt: VAD segmenter + whisper.cpp, Metal)
                      └─ text segments ─► StreamHandle (stt=local) ─► gateway ─► same events
                                                                     └─► LiveAssist (Claude)
```

* Selected per meeting in the app ("Transcrição: Deepgram / Whisper neste Mac"). Audio
  never leaves the computer; only recognized text reaches the server, which keeps question
  detection and suggestions identical to the cloud path.
* Model: `ggml-large-v3-turbo-q5_0` (~547 MB), downloaded once from the official whisper.cpp
  repository with SHA-1 verification; stored in the app's data folder.
* Built into macOS installers (`--features local-whisper`, Apple GPU); Windows builds keep
  cloud transcription only for now (the option is hidden there).

## 4. Backend (`backend/`)

FastAPI modular monolith; modules are packages under `app/` and are extracted only when
load, isolation or ownership requires it.

Implemented: settings with production guards (`app/core/config.py`); redacting JSON logs;
request IDs; health; SQLAlchemy 2 async models + Alembic migrations (`users`, `meetings`,
`usage_records`, `refresh_tokens`) — SQLite for dev/test, PostgreSQL in production; auth
(Argon2id, JWT access/stream tokens, rotating refresh tokens, login rate limit); meetings
with tenant isolation; usage metering + monthly entitlement; realtime gateway
(`app/realtime/gateway.py`); STT providers behind a protocol — `fake` (dev/test) and
`deepgram` (`app/providers/stt/deepgram.py`); LLM providers behind a protocol — `fake` and
`openai` (`app/providers/llm/`); copilot (`app/ai/`).

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
| Deepgram Nova-3 monolingual by default (`pt-BR`/`en`), `multi` optional | PT-BR support with recent model update; streaming WebSocket; per-request training opt-out; monolingual is cheaper than multilingual |
| One STT connection per audio source | Separate "you" vs "others" attribution; provider bills per stream either way |
| Native TLS (`native-tls`) in the desktop client | Uses the OS trust store (corporate proxies/CAs) on Windows/macOS |
| OpenAI GPT-5 mini via the Responses API for the copilot (configurable model) | Low cost (US$0.25 / 2.00 per M tokens, Sept 2026), structured outputs, `store: false`; swappable behind `LLMProvider` |
| Copilot context sent by the client, not kept server-side | Privacy (nothing stored), stateless server, robust to restarts |
| In-process rate limiter, concurrency and seq cache | Single instance for now; move to Redis when running several replicas (dedup is already DB-backed) |


### Conversation memory for suggestions (2026-11-18)
Every suggestion (automatic or "Como respondo?") sees the whole meeting transcript up to
`copilot_max_context_chars` (60k chars, about one hour; oldest lines drop first) plus the last 12
question/suggestion pairs of the meeting (`ActiveStreams.suggestions`, memory only, cleared when the
meeting ends). Anthropic prompt caching marks the instructions and the user's context as cacheable.


### Second brain (2026-11-24)

- **What is stored:** only what the user chose to keep, in the `memories` table on their own server.
  - Meeting summaries: opt-in per meeting. They are written once, when the meeting is finished, from the in-memory live context, which is then forgotten.
  - Notes the user writes or imports.
  - Audio and raw transcripts are never stored.
- **Search** (`app/brain/search.py`): runs locally with BM25 over paragraph-aligned passages, accent folding and a small recency boost. It returns the top passages as `<memory id date title>` blocks. A personal brain is small (hundreds of memories), so this takes milliseconds and needs no embeddings provider.
- **Use in live answers:** the gateway loads the user's memories once per stream. Each suggestion adds only clear matches: at least two different query words, or one that is in the memory's title. These are capped at 4 passages and 8k chars, placed after `<user_context>` so the cached prompt prefix is unchanged. Prompt rule 14 makes them true facts about the user, with "Meu contexto" winning on conflicts and newer memories over older ones.
- **Models:**
  - Summaries, answers and briefings use the answer model (Sonnet).
  - One summary costs about one live suggestion.
  - Asking the brain costs about US$0.01 per question with Sonnet.


### Quality loop and on-device AI (2026-11-26)

- **Learning from ratings.**
  - 👍/👎 per suggestion are stored in `suggestion_feedback`.
  - Related approved examples (BM25, strict), or the newest ones for style, are added to each answer's prompt per scenario. No fine-tuning.
- **Evals** (`backend/evals`).
  - Public fictional cases, with private real cases git-ignored.
  - Deterministic checks plus a Haiku judge, writing a Markdown report.
  - Run on each prompt change to compare against the baseline.
- **Semantic memory.**
  - fastembed multilingual MiniLM runs on the user's server. Vectors are stored per passage in `memory_vectors`.
  - BM25 and cosine results are fused by reciprocal rank.
  - The query embedding never waits for the model download.
- **Transcription engines on the Mac.**
  - Whisper (whisper.cpp, Metal).
  - Apple SpeechAnalyzer through the `meetpilot-speech` Swift sidecar: PCM in over stdin, JSON text out over stdout.
  - Both sit behind the same `Recognizer` trait and segmenter. The accuracy test computes WER for each engine on one in-memory recording.
- **Speaker labels.**
  - CAM++ embeddings (sherpa-onnx, static libraries) are computed per final phrase of the meeting audio and grouped online by cosine.
  - Only the label leaves the engine.
