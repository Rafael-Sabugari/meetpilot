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
| Audio frames never logged or persisted; sent only when the user enabled transcription for that capture, is signed in, and capture is not paused | `capture.rs`, `realtime-client` | code review; `start_request_defaults_to_no_transcription` |
| Explicit consent dialog before the first transcription (what is sent, where, retention, telling participants); cannot be accepted without ticking | `ConsentDialog.tsx` | `ConsentDialog.test.tsx`; e2e |
| Server never forwards or bills audio received while paused | `gateway.py` | `test_audio_sent_while_paused_is_never_forwarded_or_billed` |
| Deepgram requests sent with `mip_opt_out=true` (excluded from model training; retained only as long as needed to process) | `deepgram.py`, config default | `test_url_has_streaming_params_and_training_opt_out` |
| Provider key only on the server; production refuses the fake provider or a missing key | `config.py` | `test_config.py` |
| HTTPS required for non-localhost servers in the desktop client | `realtime-client/src/api.rs` | `https_required_except_localhost` |
| Passwords hashed with Argon2id; identical response for unknown user vs wrong password; login rate limited | `auth/` | `test_auth.py` |
| Access tokens 15 min; stream tokens 2 min and bound to one meeting; access/stream tokens not interchangeable | `auth/security.py` | `test_auth.py`, `test_realtime.py` |
| Refresh tokens opaque, stored hashed, rotated on use; reuse of a rotated token revokes all of the user's sessions | `auth/router.py` | `test_refresh_token_reuse_revokes_the_whole_family` |
| Desktop keeps tokens in memory only; password cleared from the form after sign-in | `account.rs`, `AccountPanel.tsx` | code review |
| Tenant isolation: other users' meetings return 404 on every route and on the stream | `meetings/router.py`, `gateway.py` | `test_users_cannot_see_or_touch_each_others_meetings`, stream tests |
| One concurrent stream per user; monthly minute quota enforced at meeting creation and mid-stream | `gateway.py`, `usage/` | `test_one_concurrent_stream_per_user`, quota tests |
| No double billing on reconnects (seq de-dup persisted with usage) | `gateway.py` | `test_replay_protection_survives_a_server_restart`; e2e (2,196 frames captured = 2,196 billed across a server restart) |
| Usage persisted even on abrupt disconnects | `gateway.py` (shielded persist) | `test_replayed_frames_after_reconnect_are_not_retranscribed_or_rebilled` |
| Transcripts not stored server-side in this version (`store_transcripts` defaults to false) | `models.py` | code review |
| Copilot: question, context and answer are never stored or logged; only counters are metered | `app/ai/router.py` | `test_ask_uses_transcript_and_meters_usage`; e2e DB check |
| Copilot: OpenAI requests sent with `store: false`; provider error bodies (which may echo prompts) are never surfaced or logged | `providers/llm/openai.py` | `test_openai_adapter_request_shape_and_parsing`, `test_openai_http_errors` |
| Copilot never invents the user's facts; placeholders force "Needs confirmation"; transcript treated as untrusted input | `app/ai/copilot.py` | `test_placeholders_force_needs_confirmation`, `test_instructions_encode_the_product_rules` |
| Copilot only for the meeting owner; monthly and per-minute limits | `app/ai/router.py` | `test_other_users_cannot_use_my_meeting`, `test_monthly_and_per_minute_limits` |
| Live assistance: rolling context kept in server RAM only for the active meeting (≤ 400 lines), removed on finish/delete; questions, answers and translations are never stored or logged — only counters | `app/realtime/assist.py`, `meetings/router.py` | `test_finishing_a_meeting_forgets_its_live_context`; e2e DB check (usage rows only) |
| Automatic suggestions can be turned off at any time, also mid-meeting; they respect the copilot quota and rate limits | `assist.py`, `questions.py`, `LiveAssistOptions.tsx` | `test_all_mode_includes_general_questions_and_off_disables`, `test_auto_answers_respect_the_monthly_quota`; e2e |
| Translation prompt treats transcript text as untrusted and only translates | `app/ai/translation.py` | code review |
| UI states that suggestions can be wrong | `CopilotPanel.tsx` | component test |
| Error events never include audio or transcript text | `gateway.py` | `test_provider_failure_is_reported_as_retryable_error` |
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

* Refresh token in the OS keychain (Windows Credential Manager / macOS Keychain).
* Email verification and password reset; account lockout policy; distributed rate limiting.
* Server-side cleanup of meetings left `active` when the client could not finish them.
* Deepgram and OpenAI data-processing agreements, retention and region review before launch
  (OpenAI API data may be retained for abuse monitoring under its policies even with
  `store: false`; confirm terms or zero-data-retention eligibility).
* Encryption at rest for documents/sensitive fields; signed short-lived object URLs.
* Data export and deletion workflows; retention jobs; audit events.
* Provider review (retention, training use, region) before each vendor is enabled; prefer
  zero-retention options where offered.
* Crash reporting with content scrubbing.

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
