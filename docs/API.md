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
| GET | `/health`, `/health/ready` | ✅ |
| POST | `/auth/register` `{email, password≥10}` → `Me` (201; 409 if taken) | ✅ |
| POST | `/auth/login` → `{access_token, expires_in, refresh_token}` (401 identical for unknown user / wrong password; 429 after 10 attempts / 5 min per email+IP) | ✅ |
| POST | `/auth/refresh` `{refresh_token}` → new pair (rotation; reuse of a rotated token revokes the whole family) | ✅ |
| POST | `/auth/logout` `{refresh_token}` → 204 (revokes) | ✅ |
| GET | `/me` | ✅ |
| POST | `/meetings` `{language: "pt-BR"\|"en"\|"multi", title?}` → `{meeting, stream: {stream_token, expires_in, stream_path}}` (402 when monthly minutes are exhausted) | ✅ |
| POST | `/meetings/{id}/stream-token` → fresh `StreamGrant` (409 if finished) | ✅ |
| GET | `/meetings`, `/meetings/{id}` | ✅ |
| POST | `/meetings/{id}/finish` | ✅ |
| DELETE | `/meetings/{id}` → 204 (cascades usage links) | ✅ |
| GET | `/usage` → `{period_start, used_seconds, limit_seconds, remaining_seconds}` | ✅ |
| GET | `/meetings/{id}/transcript`, `/summary`; POST `/copilot` | 🛠 |
| PATCH | `/me/settings` | 🛠 |
| … | knowledge, billing | 🛠 |

Tenant-scoped routes return **404** (not 403) for other users' resources. Tokens: access JWT
(HS256, 15 min, `typ=access`); stream JWT (2 min, `typ=stream`, bound to one meeting via
`mid`); refresh tokens are opaque random strings stored only as SHA-256 hashes (30 days).

## 3. Realtime WebSocket — ✅

`ws(s)://<host>/api/v1/meetings/{meeting_id}/stream`

1. Client sends, within 5 s: `{"type": "auth", "token": "<stream_token>"}`.
2. Server replies `capture.started` `{audio_format, provider, remaining_seconds}`.
3. Client sends binary frames: `[u8 source 0=system 1=microphone][u32 LE seq][640 B PCM s16le 16 kHz mono]` (645 bytes, 20 ms).
4. Client control (text): `capture.paused`, `capture.resumed`, `capture.stopped`.
5. Server events (envelope `{v:1, type, meeting_id, seq, ts, payload}`):
   * `transcript.partial` / `transcript.final` — `TranscriptSegment {segment_id, source, text, language, start_ms, end_ms, is_final}`; times are meeting-audio-relative (stable across reconnects).
   * `usage.updated` every 10 s — `{session_seconds, remaining_seconds}`.
   * `capture.paused` / `capture.resumed` acknowledgements; `capture.stopped` after final segments were flushed.
   * `error` — `{code: "stt_error"|"quota_exceeded"|"backpressure", message, retryable}` (never contains audio or transcript text).

| Close code | Meaning | Client behaviour |
|---|---|---|
| 1000 | normal | done |
| 4400 | malformed frame | fatal |
| 4401 | bad/expired/wrong-meeting token | fatal (sign in again) |
| 4402 | quota exhausted | fatal |
| 4404 | meeting missing, not yours, or finished | fatal |
| 4409 | another stream active for this user | retry with backoff |
| 1011 / 1013 | provider failure / backpressure | retry with backoff |

**Replay and de-duplication.** After any reconnect the client re-sends its last 250 frames
(5 s). The server skips frames whose `seq` it already processed for that meeting/source; the
high-water mark is persisted with usage, so this also holds across server restarts.
Audio received while paused is ignored (never forwarded or billed).
