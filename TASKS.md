# TASKS

Last updated: 2026-11-15. Legend: ✅ done · 🔄 in progress · ⛔ blocked · ⏭ next · ⏸ parked

## ✅ Completed — Free transcription with Whisper on the Mac (2026-11-16)

- ✅ Server: `stt=local` meetings/streams accept `transcript.segment` text instead of audio (no audio minutes, flood guard, replay dedupe, audio ignored); 7 tests
- ✅ Realtime client: free mode (`stt: local` auth), segments queued while disconnected and recent finals re-sent after reconnect; test with a dropping mock server
- ✅ App: on-device engine from the earlier private-mode work (VAD segmenter, decode queue, verified model download, whisper.cpp + Metal) now feeds the server; per-meeting choice "Deepgram (nuvem, pago) / Whisper neste Mac (grátis)" with model download + progress; remembered
- ✅ CI (macOS): clippy with the engine, **real Whisper inference test** (tiny model + whisper.cpp sample), installer built with `--features local-whisper`
- ⏳ Real test on the owner's Mac (download ~547 MB, Portuguese accuracy and latency)

## ✅ Completed — First run on the owner's Mac (2026-11-16)

- ✅ Installed on the owner's MacBook (arm64, macOS 26.6): one-click `Iniciar MeetPilot.command`, then LaunchAgent so the local server starts at login (`~/Library/Application Support/MeetPilot`, logs in `~/Library/Logs/MeetPilot`)
- ✅ Fixed: overlay could not be dragged (drag region only on the exact element; first click ignored while another app is active) → JS drag from anywhere except controls + grip + `acceptFirstMouse`
- ✅ Fixed: options and sign-in forgotten on every launch → capture prefs in localStorage (devices fall back to default when absent), session in the OS keychain
- ✅ Real Deepgram transcription on the Mac works (owner's first meeting, PT-BR)
- ✅ Fixed from that meeting: (1) answers full of [placeholders] → spoken first-person answers, ≤ 2 placeholders, follows style preferences in "Meu contexto"; context limit 4k → 20k chars (owner's context was truncated); (2) one question split at a pause gave two answers / requests after "Claro." or "Ótimo. E…" were missed → utterances merged until 1.5 s of silence, sentence-aware detection with fillers; (3) translations identical to the original hidden

## ✅ Completed — Every question to you + "Meu contexto"

- ✅ Two-stage detection: rules + Claude check for unclear lines (no "?", no "você", unclear target), last 8 lines + user context, ≤ 30/min, metered as `question_check`
- ✅ Answer queue: questions asked while an answer is being written are all answered in order (FIFO of 3, stale after 20 s); defaults 3 s apart, ≤ 10/min
- ✅ "Meu contexto" card (desktop, local only), sent with live settings and manual questions; new grounding label "Do seu contexto"; validated/trimmed in Rust and server (≤ 4000 chars)
- ✅ Tests: backend 141, Rust 46 (+cross Windows/macOS clippy), frontend 61
- ✅ Real Claude run: hard cases (no "?", by name, English request, statement, question to someone else) classified correctly for the user; e2e in the app: 4/4 questions answered, answers use the context ("2 anos de Kubernetes", "API Pix, mTLS"), DB/logs contain none of the context
- Known limitation: in "All questions" mode a request addressed to someone else by name may be classified as not a question.

## ✅ Completed — Claude as the AI provider + real-key run

- ✅ Anthropic (Claude) LLM adapter, default model **Claude Haiku 5.5** (cheapest; owner's choice); config, factory, production guard; 13 new tests
- ✅ Real run with the owner's Claude key (`scripts/llm_smoke.py`): ask/reply/summary/translation, 0.7–2.2 s per call; e2e in the app with real Claude (fake STT): 17 translations + 2 automatic suggestions for ≈ US$0.002
- ✅ Prompt fixes found by the real run: EN→PT translation returned the original; English answers had Portuguese placeholders; summaries said "User"/"Others" instead of "you"
- ⛔ **Deepgram real run still pending**: key created (dev, expires 2026-12-15) but this build environment's network policy blocks `api.deepgram.com`. Run `backend/scripts/deepgram_smoke.py` on a normal machine.
- Keys live only in a private file outside the repository (never committed).

## ✅ Completed — Slice 4 (live assistance: automatic suggestions + translation)

- ✅ Question detector PT/EN (`app/ai/questions.py`): trailing "?" or request opener; directed (second person / request) vs general; ignores tag questions and very short lines; no LLM cost
- ✅ Auto-answer policy: one in flight, newest waiting question wins, ≥ 6 s apart, ≤ 6/min, same question not re-answered within 60 s, stale after 20 s (configurable)
- ✅ `LiveAssist` in the realtime gateway: in-memory rolling context per meeting (cleared on finish/delete), `question.detected`, `answer.started/completed/failed`, `translation.updated`; quota-aware; metered (`copilot_request`, tokens, `translation_request`)
- ✅ `copilot.settings` control message; question ids stable across reconnects and deferred answers
- ✅ Desktop: realtime client re-sends settings after reconnect; `set_live_options` command + validated `LiveOptions` in `start_capture`; options UI (Automatic suggestions: Off / Only questions asked to me / All questions; Translate to: none / Portuguese / English), remembered between runs and changeable mid-meeting
- ✅ Transcript shows translations (original + translation / translation only / original only) and marks detected questions; overlay shows "Question detected" + suggestion and translated lines; overlay taller (380 px)
- ✅ Docs: API, architecture, security/privacy, PRD decisions, roadmap, README

### Verification performed (slice 4)

| Check | Result |
|---|---|
| Backend **113 tests** (41 new: detector PT/EN, policy, live stream flow, off/all modes, translation once per final, quota, LLM failure not billed, invalid settings, context cleared, deferred answer id) | ✅ |
| Rust **46 tests** (settings message, re-send after reconnect, live options validation), clippy -D warnings on Linux + cross Windows + cross macOS | ✅ |
| Frontend **56 tests** (auto answers in reducer, translations/questions in transcript, options UI, panels), tsc, build | ✅ |
| E2E (real app + API on PostgreSQL, fake STT/LLM, PT meeting → EN): questions marked, suggestions appear by themselves in overlay and main window, repeated question not re-answered within 60 s, translation under each line; **mid-meeting** switch to no translation and suggestions Off took effect immediately; preferences remembered after restart; DB holds only counters (6 copilot requests, 57 translations, tokens) | ✅ |
| Bug found in e2e and fixed: options were inside the fieldset disabled during capture, so they could not be changed mid-meeting | ✅ |

## ✅ Completed — Slice 3 (Ask Copilot)

- ✅ LLM provider protocol; `fake` (dev/test) and OpenAI Responses adapter (strict JSON schema, `store: false`, reasoning effort, timeouts, error mapping without leaking provider bodies)
- ✅ Context manager: client-sent transcript, newest-first character budget, chronological prompt
- ✅ Prompt rules from the brief + server-side grounding enforcement (placeholders ⇒ needs confirmation)
- ✅ `POST /meetings/{id}/copilot` with modes ask / reply / summarize, tone, language; owner-only; monthly + per-minute limits; token metering; works after Stop
- ✅ Desktop: `ask_copilot` command, answers broadcast to main window and overlay, Copilot panel (shortcuts, free question, tone, grounding badge, copy, what to confirm, remaining quota), overlay "How do I answer?" + latest answer
- ✅ Docs: API, architecture, security/privacy, PRD decisions, roadmap, README

### Verification performed (slice 3)

| Check | Result |
|---|---|
| Backend **72 tests** (17 new: endpoint, limits, isolation, errors, context budget, grounding rules, OpenAI adapter shape/errors/refusal/timeout) | ✅ |
| Rust **43 tests**, clippy -D warnings on Linux + cross Windows + cross macOS | ✅ |
| Frontend **41 tests** (reducer, context building, components), tsc, build | ✅ |
| E2E (real app + API on PostgreSQL, fake STT/LLM): overlay "How do I answer?" → answer with label in overlay **and** main window; typed question; summary; question after Stop; DB holds only counters (4 requests, tokens), no text | ✅ |

## ⏸ Parked

- ⏸ Private mode (on-device Whisper) — owner set it aside. Engine + tests on local branch `wip/whisper-private-mode` (not pushed).

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
- ⏭ Build a small PT-BR/EN evaluation set of real meeting excerpts to tune prompts on Claude Haiku 5.5 (OpenAI adapter kept, untested with a real key).
- ⛔ Hardware validation of capture on Windows and macOS (slice 1b) — still pending.

## ⏭ Next

1. Real-key runs (Deepgram, OpenAI) + hardware test plan on Windows/macOS; tune question detection and translation on real meetings.
2. Slice 5: overlay modes (minimal/standard/advanced), configurable shortcuts, multi-monitor.
3. Echo handling when the mic picks up the speakers (no headphones).
4. Refresh token in OS keychain; email verification / password reset.
5. Server job to finish meetings left `active`; Redis for rate limit/concurrency when scaling out (live context is per-process RAM today).
6. ESLint + Prettier for the frontend (currently `tsc --strict` only).

## Known limitations / tech debt

- Audio callback allocates a `Vec` per chunk (fine at ~100 chunks/s; replace with a pool if profiling shows it).
- Linear-interpolation resampler is speech-grade, not hi-fi; revisit if STT accuracy suffers.
- Windows microphone permission is detected on failure, not pre-flighted.
- macOS tap permission state cannot be queried in advance (no public API).
- Question detection is heuristic: it can miss questions without "?" from the STT punctuation or flag rhetorical ones; tune with real meetings.
- Live context lives in the API process memory: with several API instances a reconnect may land on another instance and start with an empty context (needs sticky sessions or Redis).
- Transcripts are not persisted server-side yet; copying is the only export.
- If transcription fails mid-meeting (e.g. session revoked), local capture continues until the user stops it; frames that cannot be sent are counted and shown.
- Shortcuts are fixed (configurable shortcuts planned in Slice 5).

## Velocidade do Whisper (2026-11-16)
- [x] flash attention no Metal; janela do encoder proporcional à fala (`audio_ctx`); single segment sem timestamps
- [x] segmentador: fim de fala em 500 ms, prévias a cada 2,5 s
- [x] seletor de modelo (Preciso / Rápido / Muito rápido), lembrado nas preferências
- [x] overlay mantém todas as sugestões da sessão (mais nova no topo, rolável); uma pergunta nova não apaga as respostas anteriores
- [x] sugestões também traduzidas para o idioma do usuário (`answer_translation`, mesma chamada ao Claude; só quando "Traduzir transcrição para" difere do idioma da resposta)
- [x] respostas naturais: sem "Boa pergunta"/"Claro!" no início, sem "posso detalhar se quiser"/"não tenho certeza se você quer" no fim, sem resumo repetido (regra no prompt + filtro `naturalize`); ruído de transcrição ininteligível não conta como pergunta
- [x] tradução da sugestão nos dois sentidos: resposta em português vem com versão em inglês; em inglês, com versão em português (desligada só com "Não traduzir")
- [x] seletor de tom (Natural/Direto/Técnico/Formal) no overlay; vale para as próximas sugestões e é aplicado na hora durante a reunião; a janela principal acompanha a mudança
- [x] conversa inteira para o Claude: transcrição até 60k caracteres (~1 h) e histórico das últimas 12 perguntas/sugestões da reunião (`<earlier_suggestions>`, só em memória) para perguntas que dependem de outras
- [x] perguntas sobre experiência: caso real do contexto (projeto, empresa, o que fez, como e por quê), 3–5 frases, sem inventar fatos
- [x] cache de prompt no Anthropic (instruções + "Meu contexto"), para o contexto maior não encarecer cada sugestão
- [x] tamanho da resposta conforme a pergunta: 1–2 frases (confirmação), 2–4 (normal), 5–12 (como funciona, arquitetura, cenário/troubleshooting, comparação, perguntas com várias partes, "explica melhor"); sempre falado, com transições; saída máx. 2000 tokens
