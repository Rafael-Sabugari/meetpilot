# TASKS

Last updated: 2026-11-15. Legend: ✅ done · 🔄 in progress · ⛔ blocked · ⏭ next

## ✅ Completed — Slice 2 (live transcription)

- ✅ Deepgram research (Nov 2026): Nova-3 PT-BR support, streaming pricing, `mip_opt_out` retention terms, WebSocket protocol (KeepAlive/CloseStream/Results)
- ✅ Backend data layer: SQLAlchemy 2 async models, Alembic migrations (3), verified up/down/up on PostgreSQL 17 and `alembic check` clean
- ✅ Auth: register/login/me, Argon2id, JWT access (15 min) + stream (2 min, meeting-bound) tokens, rotating refresh tokens with reuse detection, logout, login rate limit
- ✅ Meetings API with tenant isolation (404 for others' resources), stream-token refresh, finish, delete
- ✅ Usage metering in integer frames, monthly trial quota (402 / mid-stream stop)
- ✅ Realtime gateway: stream-token auth, binary frame protocol, per-source STT sessions, pause (no forwarding/billing, KeepAlive), backpressure, one stream per user, seq de-dup persisted across server restarts, shielded usage persistence, meeting-relative timestamps
- ✅ STT providers: fake (dev/test) and Deepgram adapter (tested against a protocol-faithful mock server); real-key smoke script
- ✅ `meetpilot-realtime` crate: REST client (HTTPS enforced except localhost, native TLS) and uplink (non-blocking send, 30 s buffer, reconnect with fresh token, 5 s replay, fatal close codes, flush on stop)
- ✅ Desktop: account (in-memory tokens, auto refresh), transcription toggle + language, consent dialog, live transcript panel (copy), overlay last lines, link status, dropped-frame warning
- ✅ CI: PostgreSQL service + migration checks; workspace-wide clippy/tests on Windows and macOS

### Verification performed (slice 2)

| Check | Result |
|---|---|
| Backend: ruff, mypy --strict, pytest -W error — **54 tests** | ✅ |
| Rust: fmt, clippy -D warnings (Linux + cross Windows + cross macOS), **40 tests** (11 new in realtime client) | ✅ |
| Frontend: tsc, **33 vitest tests**, vite build | ✅ |
| Migrations on PostgreSQL 17 (up/down/up, `alembic check`) | ✅ |
| E2E (Xvfb, real app + API on PostgreSQL, fake STT): register in UI → consent → PT-BR transcript in main window and overlay → **server killed and restarted mid-meeting** → reconnect, buffered audio transcribed, transcript kept → stop → **2,196 frames captured = 2,196 frames billed** | ✅ |
| E2E: server restarted with a different JWT secret → app stops retrying, clear message, dropped frames reported | ✅ |

### Bugs found and fixed during slice 2 verification

- Float accumulation refused the last frame before the quota boundary → integer-frame metering
- Frames in flight during a disconnect were silently lost → client replay + server de-dup
- De-dup was memory-only, so a server restart double-billed 5 s → high-water mark persisted with usage
- Abrupt disconnects could cancel usage persistence → shielded write
- UI cleared the transcript on every reconnect (server seq restarts) → reset only on a new capture
- Timestamps restarted at 00:00 after reconnect → offset by the session's first frame
- Stale partial text after the link ended; password left in the form after sign-in

## ✅ Completed — Slice 1 (foundation + audio capture PoC)

- ✅ Monorepo: Cargo workspace, npm workspaces, uv backend, `.gitignore`, `.env.example`, `docker-compose.yml`
- ✅ Docs: README, PRD, ARCHITECTURE, AUDIO_CAPTURE, SECURITY_PRIVACY, API, ROADMAP, original brief
- ✅ `meetpilot-audio`: `AudioCaptureProvider` / `CaptureStream` abstraction
- ✅ DSP: downmix, anti-aliased streaming resampler → 16 kHz, PCM16, 20 ms framer, levels
- ✅ `CaptureSession`: start/pause/resume/stop, status, events, pause gate, bounded non-blocking queues, drop counting + gap flags, automatic reconnect, resume-failure recovery
- ✅ Windows: WASAPI loopback + microphone (cpal 0.18)
- ✅ macOS 14.6+: CoreAudio process tap (cpal 0.18); macOS 13–14.5: ScreenCaptureKit backend (objc2); mic permission via AVFoundation; SCK permission preflight/request
- ✅ Synthetic provider with fault injection (tests + labelled demo mode)
- ✅ `mp-audio-probe` CLI (capabilities, devices, capture, opt-in WAV)
- ✅ Tauri 2 shell: commands, events, onboarding (PT/EN), capability & permission panel, device selection, capture indicator, level meters, per-source status, errors, overlay window (always-on-top, no focus steal), content-protection toggle, global shortcuts, stop-on-close
- ✅ FastAPI foundation: settings w/ production guards, redacting JSON logs, request IDs, health/readiness, realtime event schemas, STT provider protocol
- ✅ Shared TS contracts + backend↔TS event-name contract test
- ✅ CI workflow: Linux lint/test, native Windows/macOS build + test + unsigned bundle

### Verification performed

| Check | Result |
|---|---|
| `cargo fmt --check`, `cargo clippy --workspace --all-targets -D warnings` (Linux) | ✅ |
| `cargo test --workspace` — 24 audio + 3 desktop tests (session tests re-run 5× for flakiness) | ✅ |
| `cargo test -p meetpilot-audio --no-default-features` | ✅ |
| Cross type-check + clippy, `x86_64-pc-windows-msvc`, audio crate **and** Tauri app | ✅ |
| Cross type-check + clippy, `aarch64-apple-darwin`, audio crate **and** Tauri app | ✅ |
| Frontend `tsc`, vitest (16 tests), `vite build` | ✅ |
| Backend ruff, mypy `--strict`, pytest -W error (16 tests), uvicorn boot + curl | ✅ |
| Desktop app launched under Xvfb: onboarding → demo capture → overlay pause → global shortcut pause → stop from overlay → unsupported-source error shown | ✅ |
| `docker compose config` | ✅ |
| **GitHub Actions CI (run 43658671472)** — all 5 jobs green | ✅ |
| Windows runner: clippy, 27 Rust tests, probe detects `WASAPI loopback`, `.msi` + `-setup.exe` built | ✅ |
| macOS runner (arm64): clippy, 27 Rust tests, probe detects macOS version and selects `CoreAudio process tap` (objc2 code executed on real macOS), `.app` + `.dmg` built | ✅ |

## ⛔ Blocked / not verifiable in the build environment

- ⛔ **Real system-audio capture on Windows and macOS hardware** — no Windows/macOS machine
  available here. CI runners have no sound devices, so they prove build, tests and backend
  detection, not actual capture. Installers to test with are in the CI run artifacts.
  → Run `docs/AUDIO_CAPTURE.md` test plan. Owner action needed.
- ⛔ Backend Docker image not built (no Docker daemon in build environment); compose file validated only.
- ⛔ macOS signing/notarization (needs Apple Developer account); unsigned apps can behave differently with privacy permissions.

## ⛔ Blocked — needs the owner

- ⛔ **Real Deepgram run**: needs a Deepgram API key (never committed). Run `backend/scripts/deepgram_smoke.py`, then the app with `MEETPILOT_STT_PROVIDER=deepgram`.
- ⛔ Hardware validation of capture on Windows and macOS (slice 1b) — still pending.

## ⏭ Next

1. Real-key run + PT-BR accuracy/latency notes; hardware test plan on Windows/macOS.
2. Slice 3: context manager, LLM provider abstraction, manual "Ask Copilot" with grounding labels.
3. Refresh token in OS keychain; email verification / password reset.
4. Server job to finish meetings left `active`; Redis for rate limit/concurrency when scaling out.
5. ESLint + Prettier for the frontend (currently `tsc --strict` only).

## Known limitations / tech debt

- Audio callback allocates a `Vec` per chunk (fine at ~100 chunks/s; replace with a pool if profiling shows it).
- Linear-interpolation resampler is speech-grade, not hi-fi; revisit if STT accuracy suffers.
- Windows microphone permission is detected on failure, not pre-flighted.
- macOS tap permission state cannot be queried in advance (no public API).
- Overlay shows the indicator, controls and the last transcript lines; answers come with Slices 3–5.
- Transcripts are not persisted server-side yet; copying is the only export.
- If transcription fails mid-meeting (e.g. session revoked), local capture continues until the user stops it; frames that cannot be sent are counted and shown.
- Shortcuts are fixed (configurable shortcuts planned in Slice 5).
