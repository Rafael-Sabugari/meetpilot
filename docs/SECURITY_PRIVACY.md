# Security & privacy

MeetPilot handles some of the most sensitive data a person has: what is said in their
meetings. Privacy is a product feature, built in from the first slice.

## Principles

1. **No bot, no covert capture.** MeetPilot only processes audio already playing on the
   user's own computer, only after the user presses Start, with a visible indicator.
2. **User control.** Pause and stop are always one click or one shortcut away. Pausing
   guarantees no audio frames are produced (enforced and tested in `CaptureSession`).
3. **Minimal retention.** Raw audio is never stored by default. Transcript and generated
   content retention is configurable, with real deletion.
4. **Least privilege.** On macOS 14.6+ we request audio-only permission instead of screen
   recording. Microphone is requested only when the user enables it.
5. **No secrets on the client.** Provider API keys live only on the backend.
6. **No content in logs.** Transcripts, audio, prompts, model outputs and documents never
   appear in standard logs or crash reports.

## Implemented controls (this slice)

| Control | Where | Verified by |
|---|---|---|
| Capture only on explicit `start_capture` command; nothing auto-starts | `apps/desktop/src-tauri/src/capture.rs` | code review; UI flow |
| Visible capture indicator in main window and always-on-top overlay | `CaptureIndicator.tsx`, overlay window | `indicator.test.ts`, `CaptureIndicator.test.tsx`, manual |
| Indicator never understates capture (reconnecting/partial counts as capturing) | `lib/indicator.ts` | unit tests |
| Pause gate: no frames after `pause()` returns, queued audio discarded | `session.rs` | `no_frames_are_emitted_while_paused`, `pause_during_reconnect_is_honoured` |
| Closing the main window stops capture | `src-tauri/src/lib.rs` | manual |
| Audio frames not logged, not persisted, not sent anywhere (this slice) | `capture.rs` event handler | code review |
| `mp-audio-probe --wav` writes audio only when explicitly requested (dev tool) | `mp-audio-probe.rs` | code review |
| Demo mode visibly labelled DEMO | `CaptureIndicator.tsx` | unit test |
| Overlay content protection is opt-in and described as best effort | `MainView.tsx`, `set_overlay_protection` | manual |
| Backend secrets as `SecretStr`; production refuses to start without them | `app/core/config.py` | `test_config.py` |
| Production refuses `store_raw_audio_default=True` | `app/core/config.py` | `test_config.py` |
| Redacting JSON log formatter (content fields, bytes, bearer tokens, API keys) | `app/core/logging.py` | `test_logging_redaction.py` |
| Request logs contain method, path, status, duration only | `app/main.py` | code review |
| OpenAPI docs disabled in production | `app/main.py` | `test_health.py` |
| Restrictive CSP in the desktop webview | `tauri.conf.json` | config |
| `.gitignore` blocks `.env`, keys, `*.wav`/`*.pcm` | repo root | config |

## Planned controls (tracked in TASKS.md / ROADMAP.md)

* Auth: Argon2id password hashing, short-lived access tokens + rotating refresh tokens,
  rate limiting and lockout, per-request tenant authorization on every resource.
* Realtime: TLS WebSocket, token-scoped to one meeting, per-user concurrency and minute
  entitlements enforced server-side, never trusting client-supplied user IDs.
* Encryption at rest for documents/sensitive fields; signed short-lived object URLs.
* Data export and deletion workflows; retention jobs; audit events.
* Provider review (retention, training use, region) before each vendor is enabled; prefer
  zero-retention options where offered.
* Crash reporting with content scrubbing.
* Explicit first-use consent screen before any audio leaves the device (transcription).

## Consent and law

The app reminds users to inform participants and follow applicable law and organizational
policy. Brazil's LGPD and, where relevant, GDPR apply. **Technical controls do not by
themselves make the product legally compliant; qualified legal review is required before
commercial launch.**

## Screen sharing

The overlay can request exclusion from screen capture where the OS offers it. Behavior
varies by OS version and capture app. We never market the overlay as invisible.
Real results are logged in `docs/AUDIO_CAPTURE.md`.

## Reporting a vulnerability

Until a security contact is published, report privately to the repository owner.
