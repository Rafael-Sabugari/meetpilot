#!/usr/bin/env bash
# Build the `meetpilot-speech` helper (Apple SpeechAnalyzer) for the Tauri bundle.
# With the macOS 26 SDK it includes Apple's engine; with an older SDK it builds a stub that
# reports "not available", so the app still builds and falls back to Whisper.
set -euo pipefail
cd "$(dirname "$0")/.."
TRIPLE=$(rustc -vV | sed -n 's/^host: //p')
SDK_VER=$(xcrun --sdk macosx --show-sdk-version)
FLAGS=()
if [ "${SDK_VER%%.*}" -ge 26 ]; then
  FLAGS+=(-D HAS_SPEECH_ANALYZER)
  echo "macOS SDK $SDK_VER: building with Apple's SpeechAnalyzer"
else
  echo "macOS SDK $SDK_VER: too old for SpeechAnalyzer, building the stub"
fi
mkdir -p binaries
xcrun swiftc -O ${FLAGS[@]+"${FLAGS[@]}"} -target "${TRIPLE%%-apple-darwin}-apple-macos13.0" \
  speech/main.swift -o "binaries/meetpilot-speech-$TRIPLE"
# Smoke test: it must start and answer with one JSON line (ready or a clear error).
"binaries/meetpilot-speech-$TRIPLE" --check --locale pt-BR || true
