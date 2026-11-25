# API & contracts

Status legend: ✅ implemented and tested · 🛠 planned (contract draft, may change)

## 1. Desktop (Tauri) commands — ✅

`show_assistant` opens the shortcut assistant window (also ⌘⇧Space / Ctrl+Shift+Space).

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
| `set_live_options` | `{ options: { autoSuggestions: "off" \| "directed" \| "all", tone, translateTo: "pt-BR" \| "en" \| null } }` | – (sent to the server now and after every reconnect) |

`start_capture` also accepts `transcription: { language, live?: LiveOptions }` to start a
transcribed meeting with those options. Values are validated in Rust before anything is sent.

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
| GET | `/usage` → `{period_start, used_seconds, limit_seconds, remaining_seconds, copilot_requests_used, copilot_requests_limit}` | ✅ |
| POST | `/meetings/{id}/copilot` — see §2.1 | ✅ |
| GET | `/meetings/{id}/transcript`, `/summary` | 🛠 |
| PATCH | `/me/settings` | 🛠 |
| … | knowledge, billing | 🛠 |

Tenant-scoped routes return **404** (not 403) for other users' resources. Tokens: access JWT
(HS256, 15 min, `typ=access`); stream JWT (2 min, `typ=stream`, bound to one meeting via
`mid`); refresh tokens are opaque random strings stored only as SHA-256 hashes (30 days).

### 2.1 Ask Copilot — `POST /meetings/{id}/copilot` ✅

Request:
```json
{ "mode": "ask" | "reply" | "summarize",
  "question": "string (required for ask, ≤1000)",
  "transcript": [{ "source": "system"|"microphone", "text": "≤2000", "start_ms": 0 }],  // ≤500, sent by the desktop, never stored
  "tone": "natural" | "concise" | "technical" | "formal",
  "language": "pt-BR" | "en" | null,    // answer language; default = meeting language
  "user_context": "≤60000 chars | null",
  "translate_to": "pt-BR" | "en" | null } // non-null: also return the answer in the other language (pt-BR <-> en)
```
Response:
```json
{ "answer": "…", "grounding": "from_conversation" | "general_suggestion" | "needs_confirmation",
  "explanation": "…", "missing_info": ["…"], "answer_translation": "…" | null, "provider": "anthropic",
  "context": { "used_segments": 42, "total_segments": 80, "truncated": true },
  "usage": { "input_tokens": 812, "output_tokens": 96 }, "requests_remaining": 199 }
```
Errors: 404 (not your meeting), 402 (monthly copilot limit), 429 (per-minute limit),
503 (provider temporarily failed — retry), 502 (provider refused). Only counters are
recorded (`copilot_request`, `llm_input_tokens`, `llm_output_tokens`); no question, context
or answer text is stored or logged. Works for active and finished meetings.

## 3. Realtime WebSocket — ✅

`ws(s)://<host>/api/v1/meetings/{meeting_id}/stream`

1. Client sends, within 5 s: `{"type": "auth", "token": "<stream_token>"}`.
2. Server replies `capture.started` `{audio_format, provider, remaining_seconds}`.
3. Client sends binary frames: `[u8 source 0=system 1=microphone][u32 LE seq][640 B PCM s16le 16 kHz mono]` (645 bytes, 20 ms).
   **Free mode (Whisper on the user's computer):** create the meeting with `"stt": "local"`
   (allowed even with no audio minutes left) and add `"stt": "local"` to the auth message.
   The client then sends **no audio**; instead
   `{"type": "transcript.segment", "source": "system"|"microphone", "text" (≤ 2000), "start_ms", "end_ms", "is_final", "language"?}`.
   The server emits the usual `transcript.partial/final` (segment id `<source>-<start_ms>`)
   and runs live assistance on finals. Finals starting before the last delivered final of
   that source are dropped (re-sent after a reconnect); ≤ 20 segments/s; binary frames get
   one `audio_not_expected` error and are ignored; invalid segments get `bad_segment`.
   `capture.started.provider` is `"whisper-local"`. No audio minutes are metered
   (`local_audio_seconds` usage rows are informational).
4. Client control (text): `capture.paused`, `capture.resumed`, `capture.stopped`, and
   `{"type": "copilot.settings", "auto_suggestions": "off"|"directed"|"all", "tone": "natural"|"concise"|"technical"|"formal", "translate_to": "pt-BR"|"en"|null}`
   plus optional `"user_context": "<≤ 60000 chars written by the user>"`
   (any time; invalid values get an `error` with code `bad_settings`, the stream stays open).
   Defaults: `directed`, `natural`, no translation.
5. Server events (envelope `{v:1, type, meeting_id, seq, ts, payload}`):
   * `transcript.partial` / `transcript.final` — `TranscriptSegment {segment_id, source, text, language, start_ms, end_ms, is_final}`; times are meeting-audio-relative (stable across reconnects).
   * `question.detected` — `{question_id, segment_id, text, directed}`: a final line from
     *others* (system audio) that is a question; with `directed` only questions aimed at the
     user (second person, or requests like "pode explicar…"/"can you…") are reported.
     Tag questions ("Certo?", "right?") and very short ones are ignored. `via`: `"rules"`
     (free heuristic) or `"ai"` (lines the rules could not settle — no "?", no "você" —
     checked by the LLM with the last 8 lines; ≤ 30 checks/min, metered as `question_check`,
     not counted in the copilot quota).
   * `answer.started` / `answer.completed` / `answer.failed` — automatic suggestion for a
     detected question, same `question_id`. `completed` carries the Ask-Copilot answer shape
     (`answer, grounding, explanation, missing_info, answer_translation, provider, requests_remaining`;
     `answer_translation` is the answer in the `translate_to` language when it differs from
     the answer's language, else `null`) plus
     `question`. `failed`: `{question_id, question, code: "llm_error"|"copilot_quota", message, retryable}`.
     Limits: one answer in flight; questions asked meanwhile wait in a FIFO queue of 3 and are
     answered in order; ≥ 3 s apart, ≤ 10 per minute, the same question is not answered twice within 60 s, questions older than 20 s
     are dropped. Each answer counts as one copilot request.
   * `translation.updated` — `{segment_id, source, target_language, text}`: translation of
     one final line (never partials, never twice). Skipped when the meeting language already
     is the target. Backlog capped at 20 lines (an `error` `translation_backlog` is sent once).
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


### Streaming suggestions (2026-11-19)
- `answer.delta` `{question_id, text}`: the automatic suggestion written so far (full text; grows until `answer.completed`).
- `answer.translated` `{question_id, answer_translation}`: the suggestion in the other language (pt-BR ↔ en), sent right after `answer.completed` (which now carries `answer_translation: null`) when live translation is on.


## 4. Second brain — `/api/v1/brain` ✅ (2026-11-24)

Memories are summaries of meetings the user chose to remember, plus notes. Each memory belongs to one user; a memory of another user returns 404. Desktop access goes through the Tauri command `brain_request {method, path, body}`, which only allows `brain/...` paths.

| Method | Path | Body / query | Result |
|---|---|---|---|
| GET | `/brain/memories` | `q?` (search), `limit?` (≤ 500) | `MemoryOut[]`, newest first, or best match first with `q` |
| POST | `/brain/memories` | `{title, text (≤ 60k), occurred_at?}` | note `MemoryOut` (201) |
| GET | `/brain/memories/{id}` | | `MemoryOut` |
| PATCH | `/brain/memories/{id}` | `{title?, summary?, details?, suggestions?}` | `MemoryOut` |
| DELETE | `/brain/memories/{id}` | | 204 |
| POST | `/brain/ask` | `{question (≤ 2000), history?: [{role: user\|brain, text}], user_context?, assistant?: bool}` (assistant = shortcut assistant, also general questions) | `{answer, sources: [{id,title,kind,occurred_at}], input_tokens, output_tokens}` |
| POST | `/brain/briefing` | `{topic (≤ 1000), user_context?}` | same as `/ask` |

`MemoryOut` = `{id, kind: meeting|note, status: processing|ready|failed, title, occurred_at, meeting_id, summary, details (Markdown), suggestions: string[]}`.

The ask and briefing endpoints are limited to 20 requests per minute per user.

**Meeting memory:** `POST /meetings/{id}/finish` accepts an optional `{remember: bool}`. Without a body, the server uses the `remember` value from the meeting's last `copilot.settings`. When remember is on and something was transcribed, the response includes `memory_id`. The memory starts as `processing`, and the summary is written after the response.

**Settings:** `copilot.settings` (WebSocket) adds `remember` (default false) and `use_memory` (default true). `POST /meetings/{id}/copilot` adds `use_memory` (default true).
