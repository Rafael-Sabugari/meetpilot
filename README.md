# MeetPilot

**Your private AI copilot for every conversation.**

MeetPilot is a desktop app (Windows and macOS) that helps you follow and take part in online
meetings: live transcript, translation, suggested answers and summaries — shown only to you
in a private overlay.

> **MeetPilot never joins your meetings.** No bot, no virtual participant, no meeting links,
> no calendar access. It captures audio already playing on your own computer, only when you
> press Start, with a visible indicator and instant pause/stop.

## Status

Slice 1 of the roadmap: project foundation + **local audio capture proof of concept**.
See [`TASKS.md`](TASKS.md) for exactly what is implemented, tested and still blocked.
Transcription and AI features are not implemented yet.

## Repository

```text
crates/audio-capture   Rust: capture abstraction, DSP, session, OS backends, mp-audio-probe
apps/desktop           Tauri 2 app (React + TypeScript UI, Rust shell)
packages/shared-types  TypeScript contracts shared with Rust/Python
backend                FastAPI modular monolith (Python, uv)
docs                   PRD, architecture, audio capture, security/privacy, API, roadmap
```

## Prerequisites

* Rust stable (1.80+), Node 22+, Python 3.12+ with [uv](https://docs.astral.sh/uv/)
* Windows: Microsoft C++ Build Tools and WebView2 (preinstalled on Windows 11)
* macOS: Xcode Command Line Tools; macOS 13+ for system audio (14.6+ recommended)
* Linux (development only): `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libasound2-dev`

## Run the desktop app

```bash
npm ci
npm run desktop:dev        # starts Vite + the Tauri app
```

First run shows onboarding. Choose sources, press **Start capture**, and watch the level
meters and the always-on-top overlay. **Demo signal** uses a generated tone (labelled DEMO)
so you can test without devices.

Shortcuts: **Ctrl/⌘+Shift+M** pause/resume · **Ctrl/⌘+Shift+O** show/hide overlay.

## Validate audio capture on your machine

```bash
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- capabilities
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- devices
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- capture --system --seconds 15
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- capture --mic --seconds 10
```

Full test plan and OS permission details: [`docs/AUDIO_CAPTURE.md`](docs/AUDIO_CAPTURE.md).

## Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload      # http://localhost:8000/api/v1/health
# or the full stack: docker compose up --build   (from the repo root)
```

## Checks

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
npm run typecheck && npm test
(cd backend && uv run ruff check . && uv run mypy app tests && uv run pytest)
```

CI (`.github/workflows/ci.yml`) runs these on Linux and builds, tests and bundles the
desktop app natively on Windows and macOS.

## Documentation

* [Product requirements](docs/PRD.md) · [Original brief](docs/MeetPilot_Master_Brief.md)
* [Architecture](docs/ARCHITECTURE.md) · [Audio capture](docs/AUDIO_CAPTURE.md)
* [Security & privacy](docs/SECURITY_PRIVACY.md) · [API & contracts](docs/API.md)
* [Roadmap](docs/ROADMAP.md) · [Tasks](TASKS.md)

## Privacy in this version

Audio is converted and measured locally; it is **not stored and not sent anywhere**.
The only way to write audio to disk is the developer flag `mp-audio-probe --wav`.
