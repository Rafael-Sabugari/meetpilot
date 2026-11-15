# MeetPilot — Product Requirements (living summary)

The full original specification is in [`MeetPilot_Master_Brief.md`](./MeetPilot_Master_Brief.md).
This document tracks the requirements as they are being implemented and records product
decisions taken along the way.

## Vision

A private AI meeting copilot that helps a person understand and take part in online
meetings — live transcript, translation (PT ↔ EN), detected questions with suggested,
grounded answers, and post-meeting summaries — **without adding any bot or participant to
the meeting**.

**Promise:** *Your private AI copilot for every conversation.*

## Non-negotiables

1. Never joins a meeting as a bot, participant or attendee; no meeting links, invitations,
   calendar access or meeting-provider APIs required for core functionality.
2. Capture only after explicit user action, with a visible indicator at all times and
   immediate pause/stop.
3. No raw audio stored by default; storage and retention are opt-in and configurable.
4. Never fabricate the user's experience, credentials or facts; label suggestions as
   `From your profile`, `From your documents`, `General suggestion` or `Needs confirmation`.
5. Never claim the overlay is invisible in every screen share.
6. Provider keys never in the desktop app; cost controls on all AI usage.

## Users

International-team professionals, developers/consultants, sales/CS, recruiters and
candidates, managers, and anyone who needs translation support. Initial languages:
Portuguese and English. Initial markets: Brazil and international.

## Scope

| Priority | Scope |
|---|---|
| P0 (MVP) | Desktop shell (Win/macOS), onboarding + permissions, start/pause/resume/stop, system audio + optional mic, device selection, levels + capture indicator, private overlay, shortcuts, live transcript, PT/EN translation, ask-copilot, question detection + suggested answers, copy; auth, usage metering, plans/entitlements, meeting history, summary, billing abstraction, safe logs |
| P1 | Documents (PDF/DOCX/TXT), profile/knowledge base, RAG with sources, tone preferences, diarization, decisions/risks/action items, export, search, retention controls, cost dashboard |
| P2 | Teams/orgs, roles, admin/audit, SSO, integrations, optional calendar convenience, enterprise retention/residency, API, analytics |

## Product decisions log

| Date | Decision | Rationale |
|---|---|---|
| 2026-11-14 | macOS 14.6+ uses CoreAudio process taps; ScreenCaptureKit only for 13.0–14.5 | Audio-only permission instead of screen recording; no video stream. Brief allows trade-offs with explanation (rule 11) |
| 2026-11-14 | System audio and mic sent as separate tagged streams | Lets the backend attribute "me" vs "others" and avoid duplicate transcription |
| 2026-11-14 | UI ships in PT-BR and EN from day one (auto-detected) | Both launch markets |
| 2026-11-14 | "Demo signal" mode, always labelled DEMO | Lets anyone test the UI/pipeline without devices; never mistaken for real audio |
| 2026-11-14 | Closing the main window stops capture and quits | No background recording |
| 2026-11-14 | Linux is a development platform only | Not a target OS in the brief |
| 2026-11-15 | Speech-to-text: Deepgram Nova-3 (monolingual `pt-BR`/`en`; `multi` for mixed meetings) | Owner approved. Verified Nov 2026: Nova-3 supports pt-BR (model updated May 2026); streaming monolingual US$0.0048/min promotional (US$0.0077 regular), multilingual US$0.0058/min; `mip_opt_out=true` keeps audio out of training |
| 2026-11-15 | Trial plan: 60 transcription minutes / month (configurable) | Placeholder until pricing is validated; enforcement is real |
| 2026-11-15 | Transcripts are not stored server-side yet | Privacy by default; history/storage comes with the opt-in setting in a later slice |
| 2026-11-15 | On-device Whisper "private mode" explored and **set aside** by the owner; engine work kept on local branch `wip/whisper-private-mode` | Owner decision; cloud (Deepgram) stays the only transcription path for now |
| 2026-11-15 | Copilot LLM: OpenAI GPT-5 mini (Responses API, structured output, `store: false`) | Verified Sept–Oct 2026 pricing US$0.25/M input, US$2.00/M output (≈US$0.001 per question with ~3k tokens of context); model configurable |
| 2026-11-15 | Trial copilot quota: 200 questions/month, 20/minute (configurable) | Cost control until pricing is validated |
| 2026-11-15 | Automatic suggestions default to **only questions asked to the user**; options Off / All questions | Avoids noise and cost; owner approved slice 4 ("detect by itself and show the suggestion") |
| 2026-11-15 | Question detection is heuristic (no LLM) and only on the others' audio | Zero cost for non-questions; the user's own questions don't need answers |
| 2026-11-15 | Automatic answers share the copilot quota (200/month trial) | One budget the user understands; UI says so next to the setting |
| 2026-11-15 | Translation PT ↔ EN of final lines with original/translation/both views, via the same LLM | No extra vendor; partials are not translated (cost, flicker) |
| 2026-11-15 | Copilot and translation LLM: **Claude Haiku 5.5** (Anthropic), owner's choice of the cheapest Claude model; OpenAI adapter kept as an alternative | Verified Nov 2026: US$0.10 / 0.50 per M input/output tokens for prompts ≤ 100k (ours are ≤ ~5k). Measured: 0.7–2.2 s per call; a 33 s demo meeting with translation + 2 suggestions ≈ US$0.002 |
| 2026-11-15 | Recognize **every** question asked to the user: rules first, LLM check for unclear lines; questions asked while an answer is being written are queued and all answered (was: newest wins) | Owner request ("reconheça todas as perguntas que me fizer"); a real run showed a question being dropped |
| 2026-11-15 | "Meu contexto": user-written context used by suggestions, new label "Do seu contexto"; stored only on the device | Owner request; fewer `[placeholders]` while still never inventing facts beyond what the user wrote |
| 2026-11-15 | Desktop keeps the session in memory only | Avoids storing tokens on disk before keychain integration; costs a re-login per app start |

## Open product questions (need the owner's decision; not blocking current work)

1. ~~Speech-to-text vendor~~ — Deepgram chosen (2026-11-15). Still to do: measure PT-BR
   accuracy and latency on real meetings with the owner's key.
2. Brazilian payment provider alongside Stripe.
3. Whether a free tier exists and its minute allowance.
4. Product name/trademark validation ("MeetPilot" is provisional).

## Success metrics

Activation (first successful capture), time to first transcript, WAU, meetings and minutes
per user, copilot interaction rate, answer copy rate, usefulness rating, conversion, churn,
gross/contribution margin, cost per processed minute, crash-free sessions, capture failure
rate by OS/device. Hypotheses: 5–10 % free→paid, > 90 % monthly paid retention, > 70 %
contribution margin (validation goals, not forecasts).
