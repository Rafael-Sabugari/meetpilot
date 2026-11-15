# API & contracts

Status legend: ✅ implemented and tested · 🛠 planned (contract draft, may change)

## 1. Desktop (Tauri) commands — ✅

All return JSON; errors are `{ kind, message, detail? }` where `detail` is a `CaptureError`.

| Command | Args | Returns |
|---|---|---|
| `get_capabilities` | – | `{ backend, capabilities: PlatformCapabilities \| null, demo_available }` |
| `list_devices` | – | `{ inputs: DeviceInfo[], outputs: DeviceInfo[] }` |
| `request_permission` | `{ source: "system" \| "microphone" }` | `PermissionState` |
| `start_capture` | `{ request: { systemAudio: SourceSelection \| null, microphone: SourceSelection \| null, demo: boolean } }` | `CaptureStatus & { demo }` |
| `pause_capture` / `resume_capture` / `stop_capture` | – | `CaptureStatus & { demo }` |
| `get_capture_status` | – | `CaptureStatus & { demo }` |
| `set_overlay_protection` | `{ enabled }` | `boolean` |
| `set_overlay_shown` | `{ visible }` | – |

Events: `capture://status` (`CaptureStatus`), `capture://level` (`LevelReading`),
`capture://error` (`CaptureError`). Types: `packages/shared-types/src/capture.ts`.

`CaptureError.kind`: `permission_denied`, `device_not_found`, `device_unavailable`,
`unsupported`, `backend`, `invalid_config`, `invalid_state`.

## 2. REST — `/api/v1`

| Method | Path | Status |
|---|---|---|
| GET | `/health` → `{status, version, env}` | ✅ |
| GET | `/health/ready` → `{status, dependencies}` | ✅ |
| POST | `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/refresh`, `/auth/password-reset` | 🛠 |
| GET / PATCH | `/me`, `/me/settings` | 🛠 |
| POST / GET | `/meetings`, `/meetings/{id}`; POST `/meetings/{id}/finish`; DELETE `/meetings/{id}` | 🛠 |
| GET | `/meetings/{id}/transcript`, `/meetings/{id}/summary` | 🛠 |
| POST | `/meetings/{id}/copilot` | 🛠 |
| POST / GET / DELETE | `/knowledge/documents[/{id}]` | 🛠 (P1) |
| GET | `/usage`, `/billing/plans`; POST `/billing/checkout`, `/billing/webhook` | 🛠 |

All tenant-scoped routes authorize ownership server-side. Every response carries
`x-request-id`.

## 3. Realtime WebSocket — 🛠 (next slice)

`wss://<host>/api/v1/meetings/{meeting_id}/stream` with a short-lived, meeting-scoped token.

**Client → server**

* Binary frames: `[1 byte source (0=system, 1=microphone)][4 bytes seq u32 LE][640 bytes PCM]`
  — 16 kHz mono s16le, 20 ms. Max frame size enforced (`max_audio_frame_bytes`).
* JSON control: `{"type":"capture.paused"}`, `capture.resumed`, `capture.stopped`,
  `{"type":"copilot.ask","question":"…"}`, language settings.

**Server → client** — versioned envelope:

```json
{ "v": 1, "type": "transcript.partial", "meeting_id": "m_…", "seq": 42,
  "ts": "2026-11-14T23:00:00Z", "payload": { … } }
```

Types: `capture.started|paused|resumed|stopped`, `transcript.partial|final`,
`translation.updated`, `question.detected`, `answer.started|completed|failed`,
`usage.updated`, `meeting.summary.ready`, `error`. Defined in
`backend/app/realtime/events.py` and `packages/shared-types/src/realtime.ts`; equality is
enforced by `backend/tests/test_event_contract.py`.

Heartbeat every 15 s; client reconnects with backoff and resumes from last acknowledged
`seq`; server applies backpressure by closing with a retryable code instead of buffering
unbounded audio.
