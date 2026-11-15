# Audio capture — OS requirements, permissions, limitations and manual test plan

This is the highest-risk area of MeetPilot. This document is the source of truth for what
is supported, what has been verified, and how to verify it on real machines.

## Verification status (honest)

| Check | Linux (dev VM) | Windows | macOS |
|---|---|---|---|
| Core pipeline unit + session tests (24) | ✅ run | ⏳ CI job `desktop-native` | ⏳ CI job `desktop-native` |
| Audio crate type-check + clippy (real OS bindings) | ✅ | ✅ cross-checked from Linux | ✅ cross-checked from Linux (incl. ScreenCaptureKit code) |
| Desktop app type-check + clippy | ✅ built and run | ✅ cross-checked | ✅ cross-checked |
| Desktop app launched, demo capture end-to-end | ✅ (Xvfb) | ⏳ | ⏳ |
| **Real system-audio capture from a meeting** | n/a | ❌ **not yet run on hardware** | ❌ **not yet run on hardware** |

"Cross-checked" means compiled against the real Windows/Apple API bindings with
`-Zbuild-std`, without linking or running. It catches API/type errors, not runtime
behavior. Real capture must be validated with the steps below before the milestone is
called done.

## Windows 10/11

* **System audio:** WASAPI loopback on the selected output device (default: the Windows
  default output; cpal follows default-device changes).
* **Permissions:** none for loopback. Microphone requires *Settings › Privacy & security ›
  Microphone › Let desktop apps access your microphone*. Denial surfaces as
  `permission_denied` with that hint.
* **Limitations:** if the meeting app outputs to a non-default device (e.g. a USB headset),
  select that device. Apps using WASAPI exclusive mode cannot be captured. Loopback delivers
  no data while nothing is playing; the UI shows "No sound right now" rather than an error.
* **Content protection:** `SetWindowDisplayAffinity` (via Tauri). Many capture tools honor it;
  some do not. Never guaranteed.

## macOS

| Version | Backend | Permission prompt |
|---|---|---|
| 14.6+ | CoreAudio process tap (no video) | "System Audio Recording Only" (`NSAudioCaptureUsageDescription`), shown on first capture |
| 13.0 – 14.5 | ScreenCaptureKit audio | "Screen & System Audio Recording" (may require app restart after granting) |
| < 13 | unsupported | — |

* Microphone: `NSMicrophoneUsageDescription`; status via `AVCaptureDevice`.
* The tap has no public "preflight" API, so its permission shows as *asked on first use*.
* When running the unbundled `mp-audio-probe` from a terminal, macOS attributes permissions to
  the terminal app (Terminal/iTerm/VS Code). The bundled MeetPilot.app gets its own entry.
* MeetPilot's own audio is excluded on the ScreenCaptureKit path.
* Content protection uses `NSWindow.sharingType = none`; recent macOS versions and
  ScreenCaptureKit-based recorders may still capture the window. Never guaranteed.

## Manual test plan (run on each OS before closing Milestone 1)

Prerequisites: Rust stable, Node 22. From the repo root:

```bash
# 1. What does this machine support?
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- capabilities
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- devices

# 2. System audio: play a YouTube video or join a test call, then:
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- capture --system --seconds 15 --wav system.wav
#    Expect a moving level meter, frames ≈ 50/s, dropped_chunks = 0.
#    Listen to system.wav (16 kHz mono): speech should be clear. Delete it afterwards.

# 3. Microphone:
cargo run -p meetpilot-audio --bin mp-audio-probe --release -- capture --mic --seconds 10

# 4. Desktop app:
npm ci && npm run desktop:dev
```

| # | Scenario | Expected |
|---|---|---|
| 1 | Start with system audio while a meeting plays | Indicator "Capturing", overlay visible, level moves |
| 2 | Pause (button, overlay, Ctrl/⌘+Shift+M) | Indicator "Paused", frame counter frozen |
| 3 | Resume | Frames continue; no crash |
| 4 | Stop | Indicator idle, overlay hidden |
| 5 | Unplug/replug headset during capture | "Reconnecting" then "Capturing", reconnects = 1 |
| 6 | Change default output device during capture (Windows) | Audio continues on the new device |
| 7 | Deny microphone permission, start mic | Clear permission error with the settings path |
| 8 | macOS: deny System Audio Recording | Clear error; system source "failed"; app usable |
| 9 | Sleep/wake during capture | Reconnects or fails clearly; never silently stuck |
| 10 | Mic + system together | Two meters; independent status |
| 11 | Overlay over a full-screen meeting window | Stays on top; does not steal focus |
| 12 | Screen share whole desktop / one window / browser tab, protection on & off | Record the actual result per OS/app in the table below |
| 13 | Close main window during capture | Capture stops, app exits |

### Screen-sharing results log (fill in from real tests)

| OS / version | Capture app | Mode | Protection | Overlay visible to others? |
|---|---|---|---|---|
| | | | | |
