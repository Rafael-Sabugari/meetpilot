# Roadmap

Based on the 16-week plan in the brief, adjusted after the first slice. Each slice ends
with something runnable and tested.

## ✅ Slice 1 — Foundation + local audio capture PoC (this delivery)

Monorepo, docs, CI; `meetpilot-audio` (abstraction, DSP, session, WASAPI loopback,
CoreAudio tap, ScreenCaptureKit, microphone, probe CLI); Tauri 2 shell with onboarding,
capability/permission panel, device selection, capture indicator, levels, overlay,
shortcuts; FastAPI foundation with event contracts and redacting logs.

## ▶ Slice 1b — Hardware validation (needs a Windows PC and a Mac)

* Run CI on GitHub (Windows + macOS runners) and fix anything native.
* Run `docs/AUDIO_CAPTURE.md` manual test plan on Windows 10, Windows 11, macOS 13/14.5
  (ScreenCaptureKit) and macOS 15+ (tap); record results, including screen-sharing behavior.
* Sign and notarize a macOS test build (permissions behave differently for unsigned apps).

**Exit criterion = brief's Milestone 1 audio part:** real meeting audio captured on both OSes.

## Slice 2 — Streaming transcription (brief Sprint 3)

* Verify current STT vendor APIs, pricing, PT-BR/EN quality, data-retention terms (Deepgram
  first, per brief), then implement `StreamingSTTProvider` adapter + fake for tests.
* Auth minimal (register/login, short-lived tokens) so the stream is authenticated.
* Realtime WebSocket gateway (contract in `API.md`), backpressure, heartbeats, reconnect.
* Desktop: frame uploader (bounded queue, reconnect with `seq` resume), first-use consent
  screen, transcript view in main window and overlay.
* Meter processed audio minutes per user.

**Exit:** system audio transcribed and displayed live; usage minutes recorded.

## Slice 3 — Ask Copilot (completes brief Milestone 1)

Context manager (rolling window, token budget), LLM provider abstraction, manual "Ask
Copilot" with grounding labels, prompt evaluation tests, per-plan budgets.

## Slice 4 — Copilot intelligence (Sprint 4)

Question detection (staged, debounced), suggested answers, PT ↔ EN translation with
original/translated views, echo/duplicate handling between mic and system streams.

## Slice 5 — Overlay modes (Sprint 5)

Minimal/standard/advanced overlay, copy action, configurable shortcuts, multi-monitor,
focus behavior, screen-sharing test matrix.

## Slice 6 — SaaS foundation (Sprint 6)

Full auth, settings, plans/entitlements, billing abstraction (Stripe + Brazilian provider),
meeting persistence per user settings, summaries/action items, dashboard, deletion/export.

## Slice 7 — Knowledge (Sprint 7, P1)

Document upload/parsing, pgvector retrieval with sources, profile, retention settings.

## Slice 8 — Beta readiness (Sprint 8)

Test matrix, security/privacy review, latency/cost tests, packaging + auto-update,
scrubbed crash reporting, closed beta (20–30 users).
