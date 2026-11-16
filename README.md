# MeetPilot

**Your private AI copilot for every conversation.**

MeetPilot is a desktop app (Windows and macOS) that helps you follow and take part in online
meetings: live transcript, translation, suggested answers and summaries — shown only to you
in a private overlay.

> **MeetPilot never joins your meetings.** No bot, no virtual participant, no meeting links,
> no calendar access. It captures audio already playing on your own computer, only when you
> press Start, with a visible indicator and instant pause/stop.

## Status

Slices 1–4 of the roadmap: local audio capture (Windows/macOS), **live transcription**
through the MeetPilot backend (Deepgram Nova-3, PT-BR/EN), **Ask Copilot** (suggested
replies, summaries and questions about the conversation, with source labels; OpenAI), and
**live assistance**: questions asked to you are detected and a suggested answer appears by
itself in the overlay, plus PT ↔ EN translation of the transcript.
See [`TASKS.md`](TASKS.md) for exactly what is implemented, tested and still blocked.

## Repository

```text
crates/audio-capture   Rust: capture abstraction, DSP, session, OS backends, mp-audio-probe
crates/realtime-client Rust: REST client + resilient audio uplink to the backend
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
# or the full stack (PostgreSQL + Redis + API, migrations applied): docker compose up --build
```

Development uses SQLite (`meetpilot-dev.db`, created automatically) and the **fake**
speech-to-text provider, which returns placeholder text (clearly labelled in the app).

### Live transcription with Deepgram

1. Create a Deepgram API key (console.deepgram.com). Keep it on the server only.
2. Check the key with a recording (16 kHz mono WAV, e.g. from `mp-audio-probe --wav`):
   ```bash
   cd backend
   MEETPILOT_DEEPGRAM_API_KEY=... uv run python scripts/deepgram_smoke.py speech.wav pt-BR
   ```
3. Run the API with Deepgram:
   ```bash
   MEETPILOT_STT_PROVIDER=deepgram MEETPILOT_DEEPGRAM_API_KEY=... uv run uvicorn app.main:app
   ```
4. In the desktop app: create an account (server `http://localhost:8000`), tick **Live
   transcription**, accept the consent dialog, pick the meeting language, press **Start**.

Requests use `mip_opt_out=true` (audio excluded from Deepgram model training).

### Ask Copilot with Claude (default choice) or OpenAI

```bash
MEETPILOT_LLM_PROVIDER=anthropic MEETPILOT_ANTHROPIC_API_KEY=... uv run uvicorn app.main:app
# or: MEETPILOT_LLM_PROVIDER=openai MEETPILOT_OPENAI_API_KEY=...
uv run python scripts/llm_smoke.py   # real answers + translations, latency and cost
```

Claude uses **Claude Haiku 5.5** by default, the cheapest current Claude model.

In the app, during or after a transcribed capture: **How do I answer?**, **Summarize so
far**, or type a question. The overlay has a one-click **How do I answer?**. Each answer is
labelled *From the conversation*, *General suggestion* or *Needs confirmation*; the copilot
never invents your experience or credentials (it uses `[placeholders]` instead).

**Automatic suggestions** (default: only questions asked to you) and **Translate transcript
to** are next to the transcription switch and can be changed during the meeting. Each
automatic suggestion counts as one copilot question. Write about yourself and the meeting in
**My context** (kept only on your computer) and suggestions use it instead of leaving
`[blanks]`.

**Transcription: Deepgram (cloud, paid) or Whisper on this Mac (free).** Choose it under
Live transcription. Whisper runs on your computer (the audio never leaves it; only the
text goes to the server for suggestions); the first time, the app downloads the speech
model (~550 MB).

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

Audio is never stored. It leaves the computer only while **live transcription** is on
(after explicit consent), goes to your MeetPilot server over TLS, and from there to
Deepgram. Transcripts are shown to you and not saved on the server. The only way to write
audio to disk is the developer flag `mp-audio-probe --wav`.
