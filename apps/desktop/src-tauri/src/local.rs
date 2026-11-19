//! Free mode: transcription with Whisper on this computer. Audio never leaves the computer;
//! only the recognized text goes to the MeetPilot server (as `transcript.segment`), which
//! returns the usual transcript events and runs question detection and suggestions, so the
//! UI treats both engines the same way.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use meetpilot_local_stt::{
    spec, EngineConfig, LocalEvent, LocalTranscriber, ModelInfo, ModelStore, RecognizerFactory,
};
use meetpilot_realtime::{segment_message, StreamHandle};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::capture::{CommandError, EVT_SERVER};

pub const EVT_DOWNLOAD: &str = "localstt://download";

pub struct LocalState {
    store: ModelStore,
    downloads: Mutex<HashMap<String, Arc<AtomicBool>>>,
}

impl LocalState {
    pub fn new(app: &AppHandle) -> Self {
        let dir = app
            .path()
            .app_data_dir()
            .unwrap_or_else(|_| std::env::temp_dir().join("meetpilot"))
            .join("models");
        Self {
            store: ModelStore::new(dir),
            downloads: Mutex::new(HashMap::new()),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct DownloadEvent {
    pub id: String,
    /// "progress" | "done" | "error" | "cancelled"
    pub state: &'static str,
    pub downloaded: u64,
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct LocalModelsDto {
    pub available: bool,
    pub models: Vec<ModelInfo>,
}

/// Whether this build includes the Whisper engine (macOS builds, with Apple GPU).
pub const fn engine_available() -> bool {
    cfg!(feature = "local-whisper")
}

/// The model offered by default: good accuracy in Portuguese and English, fast on Apple GPUs.
pub const DEFAULT_MODEL: &str = "large-v3-turbo-q5_0";

#[tauri::command]
pub fn local_models(state: State<'_, LocalState>) -> LocalModelsDto {
    LocalModelsDto {
        available: engine_available(),
        models: state.store.list(),
    }
}

#[tauri::command]
pub async fn local_download(
    app: AppHandle,
    state: State<'_, LocalState>,
    id: String,
) -> Result<(), CommandError> {
    let spec = spec(&id)
        .filter(|s| s.listed)
        .ok_or_else(|| CommandError::new("unknown_model", format!("Unknown model {id}.")))?;
    let cancel = Arc::new(AtomicBool::new(false));
    {
        let mut d = state.downloads.lock().unwrap_or_else(|p| p.into_inner());
        if d.contains_key(&id) {
            return Err(CommandError::new(
                "busy",
                "This model is already downloading.",
            ));
        }
        d.insert(id.clone(), Arc::clone(&cancel));
    }
    let emit = {
        let app = app.clone();
        let id = id.clone();
        move |state: &'static str, downloaded: u64, total: Option<u64>, message: Option<String>| {
            let _ = app.emit(
                EVT_DOWNLOAD,
                DownloadEvent {
                    id: id.clone(),
                    state,
                    downloaded,
                    total,
                    message,
                },
            );
        }
    };
    let last = Mutex::new(Instant::now() - Duration::from_secs(1));
    let progress = |done: u64, total: Option<u64>| {
        let mut l = last.lock().unwrap_or_else(|p| p.into_inner());
        if l.elapsed() >= Duration::from_millis(200) {
            *l = Instant::now();
            emit("progress", done, total, None);
        }
    };
    let result = state.store.download(spec, progress, cancel).await;
    state
        .downloads
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .remove(&id);
    match result {
        Ok(_) => {
            emit("done", spec.approx_bytes, Some(spec.approx_bytes), None);
            Ok(())
        }
        Err(meetpilot_local_stt::ModelError::Cancelled) => {
            emit("cancelled", 0, None, None);
            Ok(())
        }
        Err(e) => {
            emit("error", 0, None, Some(e.to_string()));
            Err(CommandError::new("download_failed", e.to_string()))
        }
    }
}

#[tauri::command]
pub fn local_cancel_download(state: State<'_, LocalState>, id: String) {
    if let Some(c) = state
        .downloads
        .lock()
        .unwrap_or_else(|p| p.into_inner())
        .get(&id)
    {
        c.store(true, Ordering::SeqCst);
    }
}

#[tauri::command]
pub fn local_delete_model(state: State<'_, LocalState>, id: String) -> Result<(), CommandError> {
    let spec = spec(&id)
        .ok_or_else(|| CommandError::new("unknown_model", format!("Unknown model {id}.")))?;
    state
        .store
        .delete(spec)
        .map_err(|e| CommandError::new("io", e.to_string()))
}

/// RFC 3339 UTC timestamp without external crates (civil-from-days, Howard Hinnant).
pub fn rfc3339_now() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    rfc3339(secs)
}

pub fn rfc3339(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

fn factory(path: std::path::PathBuf, prompt: Option<String>) -> RecognizerFactory {
    #[cfg(feature = "local-whisper")]
    {
        meetpilot_local_stt::whisper::WhisperModel::factory_with_prompt(path, prompt)
    }
    #[cfg(not(feature = "local-whisper"))]
    {
        let _ = (path, prompt);
        Box::new(|| Err("Private mode is not included in this build.".to_string()))
    }
}

/// Start the on-device engine for one capture. Recognized text goes to the server through
/// `uplink`; local warnings (falling behind) are shown in the app.
pub fn start_local(
    app: &AppHandle,
    language: &str,
    model: Option<&str>,
    vocabulary: Option<String>,
    uplink: Arc<StreamHandle>,
) -> Result<Arc<LocalTranscriber>, CommandError> {
    if !engine_available() {
        return Err(CommandError::new(
            "unsupported",
            "Free transcription on this computer is not included in this build.",
        ));
    }
    let id = model.unwrap_or(DEFAULT_MODEL);
    let spec = spec(id)
        .ok_or_else(|| CommandError::new("unknown_model", format!("Unknown model {id}.")))?;
    let store = &app.state::<LocalState>().store;
    if !store.is_ready(spec) {
        return Err(CommandError::new(
            "model_missing",
            "Download the speech model before using free transcription.",
        ));
    }
    let path = store.path_of(spec);

    let seq = Arc::new(AtomicU64::new(0));
    let last_backlog = Mutex::new(Instant::now() - Duration::from_secs(60));
    let app2 = app.clone();
    let warn = move |code: &str, message: String| {
        let _ = app2.emit(
            EVT_SERVER,
            serde_json::json!({
                "v": 1,
                "type": "error",
                "meeting_id": "local",
                "seq": seq.fetch_add(1, Ordering::SeqCst),
                "ts": rfc3339_now(),
                "payload": { "code": code, "message": message, "retryable": true },
            }),
        );
    };
    let sink: meetpilot_local_stt::EventSink = Arc::new(move |ev| match ev {
        LocalEvent::Ready | LocalEvent::Stopped => {}
        LocalEvent::Segment(s) => {
            let source = match s.source {
                meetpilot_local_stt::Source::System => "system",
                meetpilot_local_stt::Source::Microphone => "microphone",
            };
            uplink.send_segment(segment_message(
                source,
                &s.text,
                s.start_ms,
                s.end_ms,
                s.is_final,
                s.language.as_deref(),
            ));
        }
        LocalEvent::Backlog { pending_finals } => {
            let mut last = last_backlog.lock().unwrap_or_else(|p| p.into_inner());
            if last.elapsed() >= Duration::from_secs(15) {
                *last = Instant::now();
                warn(
                    "local_backlog",
                    format!("This computer is falling behind ({pending_finals} phrases waiting). Cloud transcription (Deepgram) is faster."),
                );
            }
        }
        LocalEvent::Dropped { utterance_ms } => warn(
            "local_dropped",
            format!(
                "A {:.0} s passage was skipped because transcription fell too far behind.",
                utterance_ms as f64 / 1000.0
            ),
        ),
        LocalEvent::Error(e) => warn("local_error", e),
    });

    Ok(Arc::new(LocalTranscriber::start(
        factory(path, vocabulary),
        EngineConfig::for_language(language),
        sink,
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rfc3339_formats_known_instants() {
        assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(rfc3339(1_794_706_212), "2026-11-15T01:30:12Z");
    }
}
