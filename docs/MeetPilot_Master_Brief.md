# MeetPilot --- Master Product & Engineering Brief

**Document type:** Product Requirements Document (PRD) + Technical
Architecture + Implementation Playbook\
**Status:** Initial product specification\
**Working name:** MeetPilot (provisional; validate trademark and domain
availability before commercial use)\
**Target platforms:** Windows 10/11 and macOS\
**Target markets:** Brazil and international (initial languages:
Portuguese and English)\
**Business model:** Recurring-revenue SaaS, starting with individual
subscriptions and expanding to teams/businesses

------------------------------------------------------------------------

# 0. Instructions to Claude

Act as the product-minded principal engineer, software architect, UX
lead, and implementation partner for this project.

Your goal is to turn this specification into a working,
production-minded SaaS product. Do not stop at high-level advice or
generate only mockups. Inspect the existing repository and environment
first, then implement the product incrementally.

## Mandatory working rules

1.  **The AI must never join a meeting as a bot or participant.** This
    is a non-negotiable product requirement.
2.  The product is a local desktop application with a private floating
    overlay. It captures audio available on the user's own computer,
    processes it, and displays transcription and AI assistance only to
    that user.
3.  Do not use meeting-provider bots, meeting links, calendar
    invitations, virtual participants, or meeting-provider APIs as a
    requirement for core functionality.
4.  Work with system audio loopback and microphone capture using native
    operating-system capabilities and explicit user permissions.
5.  Do not promise that the overlay is invisible in every screen-sharing
    scenario. Implement and test supported privacy/display behaviors per
    operating system, and clearly communicate limitations.
6.  Do not record or transmit audio without clear user action, visible
    capture status, and appropriate consent. Provide pause/stop controls
    and privacy settings.
7.  Do not invent professional experience, credentials, facts, or user
    history in suggested answers. Distinguish known facts from
    assumptions and hypothetical examples.
8.  Prefer a modular monolith for the initial backend. Do not create
    unnecessary microservices before scale requires them.
9.  Keep the desktop app cross-platform from one codebase where
    practical, but isolate OS-specific audio capture behind well-defined
    interfaces.
10. Build working functionality, tests, error handling, telemetry hooks,
    documentation, and setup instructions. Do not claim something works
    unless it has been implemented and tested.
11. Before making major architectural choices that conflict with this
    document, explain the trade-off and choose the simplest reliable
    approach.
12. Use current, maintained SDKs and verify current provider APIs,
    pricing, platform permissions, and licensing before integrating
    them. Never hardcode API keys.
13. Do not store raw audio by default. Make retention and storage
    opt-in/configurable.
14. Never expose secrets in frontend code, desktop bundles, logs, or
    source control.
15. If a requirement cannot be implemented in the current environment,
    document the blocker, provide a safe fallback, and continue with
    independent work.

## How to work

-   First inspect the repository, installed tools, and existing code.
    Preserve useful work.
-   Produce a concise implementation plan and identify unknowns.
-   Create or update `README.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`,
    `docs/SECURITY_PRIVACY.md`, `docs/API.md`, and `docs/ROADMAP.md`.
-   Implement in vertical slices that can be run and tested.
-   After each slice, run formatting, linting, type checks, and relevant
    tests.
-   Maintain a `TASKS.md` with completed, in-progress, and blocked
    items.
-   Do not ask the user to approve every small technical decision. Make
    reasonable decisions and record assumptions. Ask only when a
    decision genuinely blocks implementation or affects a material
    product/business choice.
-   If the repository is empty, scaffold the project.
-   If a full implementation is too large for one session, complete the
    highest-value working vertical slice first and leave an explicit
    continuation plan.

------------------------------------------------------------------------

# 1. Product vision

MeetPilot is an AI meeting copilot that helps a person understand and
participate in online meetings without adding an AI bot to the meeting.

**Core promise:** Your private AI copilot for every conversation.

The desktop app runs on the user's computer. It captures audio from the
computer's output and, optionally, the user's microphone. It streams
audio to configured speech/AI services, maintains meeting context, and
presents live transcription, translation, detected questions, and
suggested answers in a private floating window.

The user remains in control: they can start, pause, resume, and stop
capture; hide or show the overlay; copy or edit suggestions; and choose
what is stored.

## Product principles

-   Bot-free: no AI attendee or meeting participant.
-   Platform-independent: work with audio from Google Meet, Microsoft
    Teams, Zoom, Webex, Discord, browser meetings, and other
    applications that output audio through the computer. Do not claim
    guaranteed compatibility with every app/device.
-   Low-latency: prioritize useful live assistance over elaborate but
    slow analysis.
-   Private by design: visible capture state, clear consent, minimal
    retention, user-controlled deletion.
-   Contextual: use user-approved profile and documents to improve
    answers.
-   Honest: clearly label suggested, inferred, uncertain, or
    hypothetical content.
-   Useful after the meeting: summaries, decisions, and action items.
-   Cost-aware: enforce usage budgets, avoid unnecessary LLM calls, and
    measure cost per active minute/user.

------------------------------------------------------------------------

# 2. Target users and use cases

## Initial segments

1.  Professionals working with international teams.
2.  Software developers, architects, consultants, and IT professionals.
3.  Sales and customer-success professionals.
4.  Recruiters and interview candidates.
5.  Managers and executives.
6.  Individuals who need translation or communication support in
    meetings.

## Core use cases

-   Understand a meeting spoken in a different language.
-   Follow a live transcript.
-   Detect a question and receive a suggested answer.
-   Ask the copilot a question without interrupting the meeting.
-   Use a personal knowledge base (CV, project notes, product
    documentation, approved company materials).
-   Receive a meeting summary and action items after the meeting.
-   Copy a suggested answer or follow-up message.

The product should not encourage deception. Suggested answers must be
grounded in user-provided facts or clearly identified as general
guidance.

------------------------------------------------------------------------

# 3. Product scope and prioritization

## P0 --- MVP

### Desktop

-   Windows and macOS desktop shell using Tauri 2, Rust, React, and
    TypeScript.
-   First-run onboarding and permissions guidance.
-   Start, pause, resume, and stop capture.
-   System audio capture:
    -   Windows: WASAPI loopback.
    -   macOS: ScreenCaptureKit, subject to OS version, permissions, and
        supported capture configuration.
-   Optional microphone capture.
-   Independent audio source controls and device selection where
    supported.
-   Audio level indicators and capture status.
-   Private, movable, resizable floating overlay.
-   Always-on-top behavior where supported.
-   Minimize/collapse and expand modes.
-   Global keyboard shortcuts.
-   Live transcript display.
-   English/Portuguese language handling and translation.
-   AI chat/input during a meeting.
-   Question detection and suggested responses.
-   Copy response action.
-   Clear error and reconnect states.
-   Visible recording/capture indicator at all times.

### SaaS/backend

-   User registration, login, logout, password reset, and session
    management.
-   Secure API authentication.
-   User profile and preferences.
-   Usage metering by processed audio minutes and AI usage.
-   Plan/entitlement model.
-   Meeting metadata and transcript storage according to user settings.
-   Post-meeting summary.
-   Basic dashboard with meeting history.
-   Basic billing integration abstraction.
-   Admin-safe operational logs without transcript/audio leakage.

### AI

-   Streaming speech-to-text integration.
-   Language detection or user-selected language.
-   Transcript segmentation and context window.
-   Question/intent detection.
-   Response generation using conversation context.
-   Portuguese ↔ English translation.
-   Basic meeting summary and action-item extraction.
-   Provider abstraction so speech and LLM providers can be replaced.

## P1 --- After MVP validation

-   Upload PDF, DOCX, and TXT documents.
-   User profile/knowledge base.
-   RAG retrieval with source references.
-   Personal response preferences and tone.
-   Distinguish user facts, retrieved facts, general knowledge, and
    assumptions.
-   Speaker labels/diarization if quality and provider support justify
    it.
-   Decisions, risks, and action-item extraction.
-   Export meeting notes.
-   Additional languages.
-   Search across meeting history.
-   Configurable retention and automatic deletion.
-   More robust usage and cost dashboard.

## P2 --- Scale and enterprise

-   Team workspaces and invitations.
-   Organization-level knowledge bases.
-   Roles and permissions.
-   Admin controls and audit logs.
-   SSO where commercially justified.
-   Slack, Notion, Salesforce, and other integrations.
-   Calendar integration as an optional convenience only; never required
    to join or capture meetings.
-   Enterprise retention policies and data residency options.
-   API and automation framework.
-   Advanced meeting analytics.

------------------------------------------------------------------------

# 4. Explicit non-goals and prohibited approaches

The following are not part of the core product:

-   No bot joining Google Meet, Teams, Zoom, Webex, or any meeting.
-   No virtual attendee.
-   No automatic meeting invitation acceptance.
-   No requirement to paste a meeting URL.
-   No requirement to install a browser extension for core capture.
-   No requirement for access to meeting-provider APIs.
-   No automatic recording without explicit user action.
-   No hidden surveillance or covert capture.
-   No guarantee that the overlay is excluded from every screen share or
    screen recording.
-   No fabricated user credentials, work history, project participation,
    or claims.
-   No unlimited AI usage without cost controls.

------------------------------------------------------------------------

# 5. User experience and screens

## 5.1 Desktop onboarding

1.  Welcome and product explanation.
2.  Explain that MeetPilot does not join meetings.
3.  Explain what audio is captured and where it is processed.
4.  Request microphone permission only when needed.
5.  Guide the user through OS-specific system-audio permissions.
6.  Let the user choose default language and translation preference.
7.  Offer an optional test recording/capture session.
8.  Explain capture indicator and keyboard shortcuts.
9.  Link to privacy and retention settings.

## 5.2 Main desktop window

-   Start meeting/capture button.
-   Audio input/output device selectors where supported.
-   Language selection.
-   Capture status.
-   Recent meetings.
-   Settings.
-   Account/plan usage.

## 5.3 Floating overlay modes

### Minimal

-   Current detected question.
-   Suggested answer.
-   Copy button.
-   Expand/collapse control.

### Standard

-   Live transcript.
-   Translation.
-   Suggested answers.
-   Ask copilot input.

### Advanced

-   Transcript and speaker labels where available.
-   Detected questions.
-   Suggested answers with confidence/grounding indicators.
-   Retrieved knowledge references.
-   Context controls.

The overlay must be readable, compact, movable, resizable, and usable
without taking focus away from the meeting unnecessarily.

## 5.4 Meeting completion

-   Stop capture.
-   Show processing status.
-   Generate summary, decisions, and action items.
-   Let user edit and delete content.
-   Offer export/copy.
-   Show usage consumed.

## 5.5 Web dashboard

-   Account and profile.
-   Meeting history.
-   Meeting detail and transcript.
-   Knowledge base/documents.
-   Preferences.
-   Subscription and usage.
-   Privacy, data export, and deletion.

The web dashboard is secondary. The desktop app is the primary product
experience.

------------------------------------------------------------------------

# 6. Technical architecture

## 6.1 Recommended stack

  ----------------------------------------------------------------------------
  Layer                   Preferred technology       Notes
  ----------------------- -------------------------- -------------------------
  Desktop shell           Tauri 2                    One cross-platform
                                                     application

  Desktop core            Rust                       Audio capture, OS
                                                     integration, lifecycle

  UI                      React + TypeScript         Overlay and desktop
                                                     screens

  Backend                 Python + FastAPI           Modular monolith
                                                     initially

  Realtime transport      WebSocket                  Audio/transcript/events

  Primary database        PostgreSQL                 SaaS and meeting metadata

  Vector search           pgvector                   Knowledge retrieval

  Cache/ephemeral state   Redis                      Sessions, queues, rate
                                                     limits

  Object storage          S3-compatible              User documents; audio
                                                     only if explicitly
                                                     enabled

  Background jobs         Celery or equivalent       Summaries, document
                                                     ingestion

  Containerization        Docker                     Local development and
                                                     deployment

  Observability           OpenTelemetry-compatible   Metrics, traces, errors
                          tools                      

  Payments                Provider abstraction       Stripe internationally if
                                                     available; Brazilian
                                                     provider as appropriate
  ----------------------------------------------------------------------------

Use a monorepo unless the existing repository suggests a better
structure.

## 6.2 High-level components

1.  Desktop application.
2.  OS-specific audio capture adapters.
3.  Audio normalization/resampling pipeline.
4.  Realtime gateway.
5.  Speech-to-text provider adapter.
6.  Transcript/context manager.
7.  AI orchestration module.
8.  Question/intent detector.
9.  Response generator.
10. Translation module.
11. Knowledge retrieval module.
12. Meeting summary/action-item module.
13. SaaS API and authentication.
14. Usage/billing module.
15. Web dashboard.
16. Observability and admin operations.

## 6.3 Audio capture abstraction

Create a platform-independent interface, for example:

``` text
AudioCaptureProvider
  - list_input_devices()
  - list_output_devices()
  - request_permissions()
  - start_system_audio_capture(config)
  - start_microphone_capture(config)
  - pause_capture()
  - resume_capture()
  - stop_capture()
  - get_capture_status()
  - subscribe_to_audio_frames(callback)
```

Implement platform-specific providers:

-   Windows: WASAPI loopback for system output; appropriate microphone
    capture API.
-   macOS: ScreenCaptureKit for supported system audio capture;
    microphone through the supported native audio path.

Do not pretend these APIs behave identically. Document OS version
requirements, permissions, device limitations, and fallback behavior.

### Audio processing requirements

-   Support a consistent internal PCM format suitable for the selected
    STT provider.
-   Resample only when necessary.
-   Use bounded buffers and backpressure.
-   Handle device changes and sleep/wake.
-   Recover from temporary network loss.
-   Avoid sending duplicate audio streams when system audio and
    microphone contain the same remote participant.
-   Keep local audio processing efficient.
-   Do not persist raw audio by default.
-   Expose capture errors in the UI without leaking sensitive data.

## 6.4 Realtime data flow

``` text
System audio + optional microphone
              |
              v
       Native capture layer
              |
              v
   Normalize / resample / buffer
              |
              v
       Secure WebSocket
              |
              v
       STT provider adapter
              |
              v
      Transcript event stream
              |
              v
     Conversation context manager
              |
       +------+------+
       |             |
       v             v
 Question/intent   Translation
 detection         (when needed)
       |
       v
 Context retrieval
       |
       v
 Response generation
       |
       v
 Overlay event stream
       |
       v
 Private desktop overlay
```

Use event types with versioned schemas. Example event names:

-   `capture.started`
-   `capture.paused`
-   `capture.resumed`
-   `capture.stopped`
-   `transcript.partial`
-   `transcript.final`
-   `translation.updated`
-   `question.detected`
-   `answer.started`
-   `answer.completed`
-   `answer.failed`
-   `usage.updated`
-   `meeting.summary.ready`

## 6.5 Backend architecture

Use a modular monolith with clear internal boundaries:

``` text
backend/
  app/
    api/
    auth/
    users/
    organizations/
    meetings/
    transcripts/
    audio/
    providers/
      stt/
      llm/
      embeddings/
    ai/
      context/
      question_detection/
      response_generation/
      translation/
      summarization/
      action_items/
    knowledge/
    billing/
    usage/
    privacy/
    jobs/
    observability/
```

Extract services only when load, deployment isolation, or team ownership
justifies it.

------------------------------------------------------------------------

# 7. AI design

## 7.1 Provider abstraction

Do not couple business logic to a single vendor. Define interfaces for:

-   Streaming STT.
-   Batch STT.
-   LLM generation.
-   Translation.
-   Embeddings.
-   Optional web search.

Provider selection must be configurable server-side. API keys remain on
the backend.

Candidate providers to evaluate: - Deepgram for streaming
transcription. - OpenAI for response generation, translation, and
summaries. - Gemini as an alternative LLM. - Other providers only after
evaluating latency, multilingual quality, pricing, retention terms, and
regional availability.

Verify current API capabilities and prices before implementation. Do not
assume a model or endpoint supports streaming, translation, or a given
language without checking current documentation.

## 7.2 Context management

Maintain two context layers:

### Short-term context

-   Recent transcript segments.
-   Current topic.
-   Recent questions.
-   Relevant participants/speaker labels if available.
-   Recent assistant suggestions.

### Long-term context

-   User-approved profile.
-   User-approved work history.
-   Uploaded documents.
-   Company/product knowledge.
-   User preferences.
-   Explicitly saved facts.

Use token budgets and relevance ranking. Do not send the entire meeting
transcript to the LLM for every request.

## 7.3 Question detection

Use a staged strategy:

1.  Wait for a sufficiently complete transcript segment.
2.  Detect interrogative intent and conversational cues.
3.  Determine whether the question appears directed to the user or is a
    general discussion question.
4.  Avoid triggering on every rhetorical question.
5.  Debounce and deduplicate similar events.
6.  Let the user manually ask for a response if automatic detection
    misses something.

Show a subtle notification rather than constantly interrupting the user.

## 7.4 Response generation

Responses should: - Be concise by default. - Match the selected
language. - Match the selected tone (natural, concise, technical,
formal). - Use recent conversation context. - Retrieve relevant
user-approved facts. - Cite or identify retrieved knowledge when
possible. - Flag missing information. - Never invent personal experience
or qualifications. - Offer a clarification question or safe general
answer when context is insufficient.

Suggested answer structure: - Suggested response. - Optional short
explanation. - Grounding label: `From your profile`,
`From your documents`, `General suggestion`, or `Needs confirmation`.

## 7.5 Translation

-   Support Portuguese and English initially.
-   Preserve technical terminology and product names.
-   Allow original and translated transcript views.
-   Do not translate every event twice unnecessarily.
-   Make language selection explicit and allow automatic detection with
    user override.

## 7.6 Summaries and action items

After the user stops capture: - Generate a concise summary. - Extract
decisions. - Extract action items. - Identify owner and due date only
when explicitly stated or clearly supported. - Mark unknown
owner/deadline as unspecified. - Allow edits before export. - Do not
silently send messages or create external tasks in the MVP.

------------------------------------------------------------------------

# 8. Data model

Design a normalized relational schema. At minimum consider:

-   `users`
-   `user_settings`
-   `subscriptions`
-   `plans`
-   `usage_records`
-   `meetings`
-   `transcript_segments`
-   `meeting_summaries`
-   `meeting_action_items`
-   `knowledge_documents`
-   `document_chunks`
-   `user_profiles`
-   `ai_requests`
-   `provider_usage`
-   `audit_events`
-   `organizations` (P2)
-   `organization_members` (P2)

Important design requirements: - Every tenant-owned row must have
ownership/tenant boundaries. - Enforce authorization in the backend, not
only the frontend. - Add indexes for meeting history and time-based
queries. - Use soft deletion only where legally and operationally
appropriate; support actual deletion workflows. - Do not store audio
blobs in PostgreSQL. - Do not store raw audio unless the user explicitly
enables it and understands the retention policy. - Define retention
policies for transcript and generated content. - Keep provider
request/response logs free of transcript content by default.

------------------------------------------------------------------------

# 9. API and realtime contracts

Create versioned REST endpoints, for example:

``` text
POST   /api/v1/auth/register
POST   /api/v1/auth/login
POST   /api/v1/auth/logout
GET    /api/v1/me
PATCH  /api/v1/me/settings

POST   /api/v1/meetings
GET    /api/v1/meetings
GET    /api/v1/meetings/{meeting_id}
POST   /api/v1/meetings/{meeting_id}/finish
DELETE /api/v1/meetings/{meeting_id}

GET    /api/v1/meetings/{meeting_id}/transcript
GET    /api/v1/meetings/{meeting_id}/summary

POST   /api/v1/meetings/{meeting_id}/copilot
POST   /api/v1/knowledge/documents
GET    /api/v1/knowledge/documents
DELETE /api/v1/knowledge/documents/{document_id}

GET    /api/v1/usage
GET    /api/v1/billing/plans
POST   /api/v1/billing/checkout
POST   /api/v1/billing/webhook
```

The exact endpoints may change, but document the contracts and validate
inputs.

Realtime: - Authenticated WebSocket connection. - Meeting/session scoped
authorization. - Audio frames sent in bounded chunks. - Server sends
transcript and AI events. - Heartbeats, reconnect, backpressure, and
graceful shutdown. - Enforce per-user concurrency and usage
entitlements. - Never trust a client-supplied user ID without validating
the authenticated session.

------------------------------------------------------------------------

# 10. Desktop overlay and privacy behavior

## Required behavior

-   Overlay is a separate desktop window.
-   Movable and resizable.
-   Always-on-top where supported.
-   Can be collapsed to a small control.
-   Keyboard shortcuts configurable.
-   Does not steal focus unnecessarily.
-   Shows a visible active-capture indicator.
-   Provides immediate pause and stop controls.
-   Provides a setting to hide the overlay from supported screen-capture
    paths when the OS offers that capability.
-   Clearly explains that screen-sharing behavior varies by operating
    system and application.

Do not implement deceptive stealth behavior. The product is private to
the user in normal use, but capture must remain user-controlled and
transparent.

Test: - Sharing the full desktop. - Sharing a single application
window. - Sharing a browser tab. - Screen recording. - Multiple
monitors. - Overlay on top of meeting window. - OS permission denial. -
Sleep/wake and device changes.

------------------------------------------------------------------------

# 11. Security, privacy, and compliance

Build privacy into the product from the beginning.

Requirements: - TLS for all network communication. - Secure secret
management. - Password hashing using a modern password-hashing
algorithm. - Short-lived access tokens and secure refresh/session
handling. - Authorization checks on every tenant-scoped resource. - Rate
limits and abuse protection. - Encryption at rest for stored documents
and sensitive data. - Signed, short-lived object-storage URLs. - No API
keys in the desktop client. - No transcript/audio in standard
application logs. - Data export and deletion workflows. - Configurable
retention. - Clear privacy notice and terms. - Explicit user control
over capture. - Consent guidance for recording/transcription according
to applicable law and meeting policies. - Vendor review for data
processing, retention, and training-use terms.

Plan for Brazil's LGPD and applicable international privacy laws,
including GDPR where relevant. Obtain qualified legal review before
commercial launch. Do not claim legal compliance solely because
technical controls exist.

------------------------------------------------------------------------

# 12. Usage metering and unit economics

Track at least: - Audio minutes processed. - STT provider cost. - LLM
input/output tokens. - Translation usage. - Embedding/document
processing usage. - Storage. - Number of AI responses. - Cost per active
user. - Cost per meeting. - Cost per processed minute.

Do not call a high-cost LLM for every transcript fragment.

Recommended optimization: 1. Stream transcription. 2. Maintain a rolling
context window. 3. Run lightweight intent/question detection. 4. Trigger
advanced LLM generation only for relevant events or explicit user
requests. 5. Retrieve only relevant knowledge chunks. 6. Cache stable
profile/document retrieval where safe. 7. Enforce plan budgets and alert
users before limits. 8. Provide internal cost dashboards.

Initial pricing hypotheses (validate with real costs and
willingness-to-pay):

### Brazil

-   Starter: R\$ 29.90/month --- 200 minutes.
-   Pro: R\$ 59.90/month --- 800 minutes.
-   Premium: R\$ 99.90/month --- 1,800 minutes.

### International

-   Starter: US\$ 7.99/month.
-   Pro: US\$ 14.99/month.
-   Premium: US\$ 24.99/month.

These are test prices, not validated final prices. Do not launch a plan
until realistic usage simulations show sustainable gross margin.
Consider annual plans, extra-minute packs, and team pricing later.

------------------------------------------------------------------------

# 13. Testing strategy

## Unit tests

-   Audio frame handling.
-   Transcript context window.
-   Question detector.
-   Prompt/context assembly.
-   Usage metering.
-   Billing entitlement logic.
-   Authorization and tenant isolation.
-   Retention/deletion logic.

## Integration tests

-   STT provider adapter.
-   LLM provider adapter.
-   WebSocket event flow.
-   Database persistence.
-   Document ingestion and retrieval.
-   Billing webhook idempotency.

## Desktop tests

-   Start/stop/pause capture.
-   OS permission flows.
-   Device switching.
-   Audio reconnection.
-   Overlay interactions.
-   Global shortcuts.
-   Multiple monitors.
-   Screen-sharing behavior.
-   Windows and macOS packaging.

## End-to-end acceptance scenarios

1.  User installs app, grants permissions, and starts capture.
2.  Audio from a meeting is transcribed without a bot joining.
3.  User sees transcript in the private overlay.
4.  A question is detected and a grounded answer appears.
5.  User can manually ask the copilot a question.
6.  User pauses capture; no audio is transmitted while paused.
7.  User stops capture and receives a summary.
8.  Usage minutes are correctly recorded.
9.  User deletes a meeting and its associated data according to policy.
10. A user cannot access another user's meeting, transcript, document,
    or usage.
11. The app handles network interruption and recovers without corrupting
    the meeting.
12. The app clearly communicates when a platform permission is
    unavailable.

------------------------------------------------------------------------

# 14. Performance and reliability targets

Initial engineering targets to validate:

  -----------------------------------------------------------------------
  Metric                              Initial target
  ----------------------------------- -----------------------------------
  Time to start transcript            Under 5 seconds in normal
                                      conditions

  Partial transcript latency          P95 under 1.5 seconds where
                                      provider/network allow

  Suggested answer latency            P95 under 5 seconds after a
                                      complete detected question

  Backend availability                99.5% initial target

  Audio reconnect                     Automatic with clear UI status

  Memory usage                        Measure and optimize per OS; avoid
                                      arbitrary guarantees

  CPU usage                           Keep capture and overlay
                                      lightweight

  Data loss                           No silent loss; surface gaps and
                                      reconnect state
  -----------------------------------------------------------------------

These are targets, not guarantees. Measure real performance under
different networks, devices, languages, and providers.

------------------------------------------------------------------------

# 15. Development roadmap --- 16 weeks

Assume one primary developer with significant weekly availability and
occasional specialist help. Adjust after repository inspection and an
audio-capture spike.

## Sprint 1 --- Foundation (Weeks 1--2)

-   Inspect repository and define monorepo.
-   Scaffold Tauri 2 + React + TypeScript.
-   Scaffold FastAPI backend.
-   Configure PostgreSQL and local Docker environment.
-   Define shared types and event schemas.
-   Create basic onboarding and app shell.
-   Add CI, formatting, linting, and tests.

## Sprint 2 --- Native audio (Weeks 3--4)

-   Implement Windows WASAPI loopback spike.
-   Implement macOS ScreenCaptureKit spike.
-   Implement microphone capture.
-   Build platform adapter interface.
-   Add permission and device handling.
-   Test audio format, buffering, and reconnect.
-   Document OS requirements and limitations.

**Milestone:** reliable local audio capture on both platforms before
building advanced AI.

## Sprint 3 --- Streaming transcription (Weeks 5--6)

-   Add STT provider abstraction.
-   Integrate selected streaming provider.
-   Implement authenticated WebSocket.
-   Render partial/final transcript segments.
-   Add language handling.
-   Add reconnect/backpressure.
-   Meter audio minutes.

## Sprint 4 --- Copilot intelligence (Weeks 7--8)

-   Build context manager.
-   Implement question detection.
-   Add response generation.
-   Add translation.
-   Add user-triggered ask-copilot flow.
-   Add prompt evaluation tests.
-   Add grounding labels.

## Sprint 5 --- Desktop overlay (Weeks 9--10)

-   Implement floating window.
-   Add always-on-top where supported.
-   Add move/resize/collapse behavior.
-   Add shortcuts.
-   Add transcript and answer views.
-   Add pause/stop and capture indicator.
-   Test focus and screen-sharing behavior.

## Sprint 6 --- SaaS foundation (Weeks 11--12)

-   Authentication and account settings.
-   Usage and plan entitlements.
-   Subscription integration abstraction.
-   Dashboard and meeting history.
-   Meeting persistence.
-   Summary generation.
-   Data deletion controls.

## Sprint 7 --- Knowledge and insights (Weeks 13--14)

-   Document upload and parsing.
-   Embeddings and pgvector retrieval.
-   Profile/knowledge context.
-   Decisions and action items.
-   Export and editing.
-   Retention settings.

## Sprint 8 --- Beta and launch readiness (Weeks 15--16)

-   Windows/macOS test matrix.
-   Security and privacy review.
-   Latency and cost tests.
-   Packaging and update strategy.
-   Crash reporting without sensitive content.
-   Onboarding and help content.
-   Closed beta with 20--30 users.
-   Fix critical issues and prepare public beta.

------------------------------------------------------------------------

# 16. Repository structure proposal

Adapt this to the actual repository rather than blindly creating every
folder:

``` text
meetpilot/
├── apps/
│   ├── desktop/
│   │   ├── src/
│   │   ├── src-tauri/
│   │   └── tests/
│   └── web/
│       ├── src/
│       └── tests/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── meetings/
│   │   ├── transcripts/
│   │   ├── audio/
│   │   ├── providers/
│   │   │   ├── stt/
│   │   │   ├── llm/
│   │   │   └── embeddings/
│   │   ├── ai/
│   │   ├── knowledge/
│   │   ├── billing/
│   │   ├── usage/
│   │   ├── privacy/
│   │   └── jobs/
│   └── tests/
├── packages/
│   ├── shared-types/
│   ├── ui/
│   └── prompts/
├── infrastructure/
│   ├── docker/
│   ├── database/
│   └── deployment/
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── SECURITY_PRIVACY.md
│   ├── API.md
│   ├── DATA_MODEL.md
│   ├── UX_SPEC.md
│   └── ROADMAP.md
├── .env.example
├── docker-compose.yml
├── README.md
└── TASKS.md
```

------------------------------------------------------------------------

# 17. Launch and go-to-market

## Initial positioning

"Your private AI copilot for every conversation."

Lead with: - No bot joins the meeting. - Private desktop overlay. - Live
transcription and translation. - Contextual suggested answers. - Meeting
summaries and action items.

Avoid claims such as "undetectable", "invisible in every screen share",
"100% accurate", or "works with every app".

## Initial acquisition

### Brazil

-   LinkedIn product demos.
-   Developer and consultant communities.
-   Remote-work communities.
-   Content about English meetings and professional communication.
-   Small beta cohort and referral program.

### International

-   Product Hunt.
-   Relevant Reddit communities, respecting community rules.
-   YouTube and short-form demos.
-   SEO for AI meeting copilot and real-time meeting assistant.
-   Affiliate/creator partnerships after retention is validated.

## Beta stages

1.  Closed beta: 20--30 users.
2.  Public beta: 100--200 users.
3.  First paid cohort.
4.  Validate retention, conversion, support load, and gross margin.
5.  Expand language and team capabilities only after core product
    retention is promising.

------------------------------------------------------------------------

# 18. Success metrics

Track: - Activation: user completes first successful capture. - Time to
first transcript. - Weekly active users. - Meetings per active user. -
Minutes processed per user. - Percentage of meetings with at least one
copilot interaction. - Answer copy/use rate. - User-rated answer
usefulness. - Free-to-paid conversion. - Monthly churn. - Gross margin
and contribution margin. - Cost per processed minute. - Crash-free
desktop sessions. - Capture failure rate by OS and device.

Initial hypotheses to test: - Free-to-paid conversion: 5--10%. - Monthly
paid retention: above 90%. - Contribution margin: above 70%.

These are validation goals, not forecasts.

------------------------------------------------------------------------

# 19. Immediate execution checklist

Start with this exact order:

-   [ ] Inspect the repository and identify existing code.
-   [ ] Report the current state and implementation risks.
-   [ ] Create/update product and architecture documentation.
-   [ ] Scaffold or adapt the monorepo.
-   [ ] Build a minimal Tauri desktop shell.
-   [ ] Prove Windows system-audio capture.
-   [ ] Prove macOS system-audio capture.
-   [ ] Prove microphone capture.
-   [ ] Implement a local audio test screen with visible capture state.
-   [ ] Add streaming transcription for one provider.
-   [ ] Render transcript events in the desktop UI.
-   [ ] Implement authenticated backend streaming.
-   [ ] Add the first private overlay.
-   [ ] Add a manual "Ask Copilot" action.
-   [ ] Add question detection and suggested answers.
-   [ ] Add translation.
-   [ ] Add SaaS authentication and usage tracking.
-   [ ] Add meeting summary.
-   [ ] Add knowledge base only after the core loop works.
-   [ ] Run cross-platform tests and document limitations.

## First milestone definition of done

The first milestone is complete only when:

-   The desktop app runs on Windows and macOS, or any unsupported
    platform is explicitly identified with a tested implementation plan.
-   The user can start and stop local audio capture.
-   The app shows a visible capture status.
-   No bot or virtual participant joins the meeting.
-   System audio is transcribed and displayed in the desktop app.
-   The user can manually ask the AI about the current conversation.
-   Errors and permission failures are visible and recoverable.
-   Setup and test instructions are documented.

------------------------------------------------------------------------

# 20. Final instruction

Begin by inspecting the current repository and environment. Do not
assume the repository is empty or that the proposed stack is already
installed.

Then: 1. Summarize the repository state. 2. Identify the highest-risk
technical unknowns, especially native audio capture on Windows and
macOS. 3. Propose a short, ordered implementation plan. 4. Create/update
the documentation. 5. Start implementing the first vertical slice:
desktop shell + local audio capture proof of concept. 6. Keep `TASKS.md`
updated and report exactly what is implemented, tested, and still
blocked.

**The defining product requirement must remain true throughout
development: MeetPilot is a private desktop AI copilot. It captures
audio locally from the user's computer with explicit user control. It
never joins the meeting as a bot, participant, or attendee.**
