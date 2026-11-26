//! Transcription test: the user reads a known text aloud; each available engine
//! (Whisper, Apple) transcribes the same recording and we report the word error rate.
//!
//! The recording only lives in memory for this test (never written, never sent) and is
//! dropped as soon as the results are computed. The capture indicator is shown in the UI
//! while it records, and it stops by itself after `MAX_SECONDS`.

use std::sync::{Arc, Mutex};
use std::time::Instant;

use meetpilot_audio::{
    default_provider, AudioSource, CaptureConfig, CaptureSession, SessionEvent, SourceSelection,
};
use meetpilot_local_stt::apple::{locale_for, AppleSpeech};
use meetpilot_local_stt::engine::normalize;
use meetpilot_local_stt::wer::wer;
use meetpilot_local_stt::{spec, EngineConfig, RecognizerFactory};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::capture::CommandError;
use crate::local::{apple_helper, engine_available, LocalState, DEFAULT_MODEL};

/// Whisper handles up to 30 s in one pass; the reading text takes about 20 s.
pub const MAX_SECONDS: usize = 28;
const RATE: usize = 16_000;
pub const EVT_BENCH_LEVEL: &str = "bench://level";

#[derive(Default)]
pub struct BenchState {
    running: Mutex<Option<Running>>,
}

struct Running {
    session: CaptureSession,
    audio: Arc<Mutex<Vec<i16>>>,
}

#[derive(Debug, Serialize)]
pub struct EngineResult {
    /// "whisper" | "apple"
    pub engine: String,
    /// Model or engine detail shown to the user.
    pub label: String,
    /// Word error rate, 0..1 (lower is better); None when the engine failed.
    pub wer: Option<f64>,
    pub seconds: f64,
    pub text: String,
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct BenchResult {
    pub audio_seconds: f64,
    pub results: Vec<EngineResult>,
}

#[tauri::command]
pub fn benchmark_start(app: AppHandle, state: State<'_, BenchState>) -> Result<(), CommandError> {
    let mut running = state.running.lock().unwrap_or_else(|p| p.into_inner());
    if running.is_some() {
        return Err(CommandError::new(
            "busy",
            "A transcription test is already recording.",
        ));
    }
    let provider = default_provider().ok_or_else(|| {
        CommandError::new(
            "unsupported",
            "Audio capture is not available on this computer.",
        )
    })?;
    let audio = Arc::new(Mutex::new(Vec::<i16>::with_capacity(MAX_SECONDS * RATE)));
    let sink = Arc::clone(&audio);
    let app2 = app.clone();
    let handler: meetpilot_audio::EventHandler = Arc::new(move |ev| match ev {
        SessionEvent::Frame(f) if f.source == AudioSource::Microphone => {
            let mut a = sink.lock().unwrap_or_else(|p| p.into_inner());
            let room = MAX_SECONDS * RATE - a.len().min(MAX_SECONDS * RATE);
            a.extend(f.samples.iter().take(room));
        }
        SessionEvent::Level(l) => {
            let _ = app2.emit(EVT_BENCH_LEVEL, l);
        }
        _ => {}
    });
    let session = CaptureSession::new(provider, handler);
    session
        .start(&CaptureConfig {
            system_audio: None,
            microphone: Some(SourceSelection { device_id: None }),
        })
        .map_err(|e| CommandError::new("capture", e.to_string()))?;
    *running = Some(Running { session, audio });
    Ok(())
}

#[tauri::command]
pub fn benchmark_cancel(state: State<'_, BenchState>) {
    if let Some(r) = state
        .running
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .take()
    {
        r.session.stop();
    }
}

fn whisper_factory(app: &AppHandle, model: Option<&str>) -> Option<(String, RecognizerFactory)> {
    if !engine_available() {
        return None;
    }
    let id = model
        .filter(|m| *m != crate::local::APPLE_MODEL)
        .unwrap_or(DEFAULT_MODEL);
    let spec = spec(id)?;
    let store = &app.state::<LocalState>().store;
    if !store.is_ready(spec) {
        return None;
    }
    Some((
        format!("Whisper {id}"),
        crate::local::factory(store.path_of(spec), None),
    ))
}

fn run_engine(
    engine: &str,
    label: String,
    factory: RecognizerFactory,
    audio: &[f32],
    language: &str,
    reference: &str,
) -> EngineResult {
    let started = Instant::now();
    let out = factory().and_then(|mut r| r.transcribe(audio, language));
    let seconds = started.elapsed().as_secs_f64();
    match out {
        Ok(text) => EngineResult {
            engine: engine.into(),
            label,
            wer: Some(wer(reference, &text)),
            seconds,
            text,
            error: None,
        },
        Err(e) => EngineResult {
            engine: engine.into(),
            label,
            wer: None,
            seconds,
            text: String::new(),
            error: Some(e),
        },
    }
}

/// Stop recording and compare every engine available here on the same audio.
#[tauri::command]
pub async fn benchmark_finish(
    app: AppHandle,
    state: State<'_, BenchState>,
    reference: String,
    language: String,
    model: Option<String>,
) -> Result<BenchResult, CommandError> {
    let running = state
        .running
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .take()
        .ok_or_else(|| CommandError::new("not_recording", "Start the test first."))?;
    running.session.stop();
    let samples = std::mem::take(&mut *running.audio.lock().unwrap_or_else(|p| p.into_inner()));
    drop(running);
    if samples.len() < RATE {
        return Err(CommandError::new(
            "too_short",
            "Nothing was recorded. Check the microphone.",
        ));
    }
    let mut audio: Vec<f32> = samples.iter().map(|&s| s as f32 / 32_768.0).collect();
    drop(samples);
    normalize(&mut audio);
    let audio_seconds = audio.len() as f64 / RATE as f64;
    let cfg = EngineConfig::for_language(&language);
    let whisper = whisper_factory(&app, model.as_deref());
    let apple = apple_helper();
    let results = tauri::async_runtime::spawn_blocking(move || {
        let mut results = Vec::new();
        if let Some((label, f)) = whisper {
            results.push(run_engine(
                "whisper",
                label,
                f,
                &audio,
                &cfg.whisper_language,
                &reference,
            ));
        }
        if let Some(helper) = apple {
            let f = AppleSpeech::factory(helper, locale_for(&cfg.whisper_language).to_string());
            results.push(run_engine(
                "apple",
                "Apple (macOS)".into(),
                f,
                &audio,
                &cfg.whisper_language,
                &reference,
            ));
        }
        results // the audio is dropped here
    })
    .await
    .map_err(|e| CommandError::new("internal", e.to_string()))?;
    if results.is_empty() {
        return Err(CommandError::new(
            "no_engine",
            "No on-device engine is available: download a Whisper model first.",
        ));
    }
    Ok(BenchResult {
        audio_seconds,
        results,
    })
}
