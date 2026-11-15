# TASKS

Last updated: 2026-11-14. Legend: ✅ done · 🔄 in progress · ⛔ blocked · ⏭ next

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

## ⏭ Next (Slice 1b → 2)

1. Execute hardware test plan; fix issues; record screen-sharing results.
2. Choose STT vendor after checking current API/pricing/retention (decision needed from owner).
3. Minimal auth + authenticated realtime WebSocket + STT adapter + transcript UI.
4. First-use consent screen before any audio leaves the device.
5. Add ESLint + Prettier to the frontend (currently `tsc --strict` only).

## Known limitations / tech debt

- Audio callback allocates a `Vec` per chunk (fine at ~100 chunks/s; replace with a pool if profiling shows it).
- Linear-interpolation resampler is speech-grade, not hi-fi; revisit if STT accuracy suffers.
- Windows microphone permission is detected on failure, not pre-flighted.
- macOS tap permission state cannot be queried in advance (no public API).
- Overlay currently shows the capture indicator and controls only; transcript/answers come with Slices 2–5.
- Shortcuts are fixed (configurable shortcuts planned in Slice 5).
