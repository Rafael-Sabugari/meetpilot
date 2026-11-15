//! Tauri commands and events for user-controlled capture and (optional) live transcription.
//!
//! Events emitted to the webviews:
//! * `capture://status`   — [`CaptureStatus`] whenever the session or a source changes state.
//! * `capture://level`    — [`LevelReading`] ~10x/s per source (drives the visible indicator).
//! * `capture://error`    — [`CaptureError`] for non-fatal problems (e.g. one source failed).
//! * `realtime://status`  — [`LinkStatus`] of the connection to the MeetPilot server.
//! * `realtime://event`   — server envelopes (transcripts, usage, errors), passed through.
//!
//! Audio leaves the computer only when the user enabled transcription for this capture,
//! is signed in, and capture is active and not paused. Frames are never logged.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use meetpilot_audio::providers::synthetic::SyntheticProvider;
use meetpilot_audio::{
    default_provider, AudioCaptureProvider, AudioSource, CaptureConfig, CaptureError,
    CaptureSession, CaptureStatus, DeviceInfo, PermissionState, PlatformCapabilities, SessionEvent,
    SessionState, SourceSelection,
};
use meetpilot_realtime::{
    spawn_stream, EventSink, Source, StreamConfig, StreamEvent, StreamHandle, TokenProvider,
};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::account::AccountState;

pub const EVT_STATUS: &str = "capture://status";
pub const EVT_LEVEL: &str = "capture://level";
pub const EVT_ERROR: &str = "capture://error";
pub const EVT_LINK: &str = "realtime://status";
pub const EVT_SERVER: &str = "realtime://event";
pub const OVERLAY_LABEL: &str = "overlay";

#[derive(Debug, Serialize)]
pub struct CommandError {
    pub kind: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<CaptureError>,
}

impl From<CaptureError> for CommandError {
    fn from(e: CaptureError) -> Self {
        let kind = serde_json::to_value(&e)
            .ok()
            .and_then(|v| v.get("kind").and_then(|k| k.as_str()).map(str::to_owned))
            .unwrap_or_else(|| "capture_error".into());
        Self {
            kind,
            message: e.to_string(),
            detail: Some(e),
        }
    }
}

impl CommandError {
    pub fn new(kind: &str, message: impl Into<String>) -> Self {
        Self {
            kind: kind.into(),
            message: message.into(),
            detail: None,
        }
    }
}

type CmdResult<T> = Result<T, CommandError>;

/// Live link to the server for one meeting.
struct Uplink {
    handle: Arc<StreamHandle>,
    meeting_id: String,
}

struct Active {
    session: CaptureSession,
    demo: bool,
    uplink: Option<Uplink>,
}

#[derive(Default)]
pub struct CaptureState {
    active: Mutex<Option<Active>>,
    /// Most recent transcribed meeting, kept after Stop so the user can still ask about it.
    last_meeting: Mutex<Option<String>>,
}

impl CaptureState {
    fn lock(&self) -> std::sync::MutexGuard<'_, Option<Active>> {
        self.active.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Meeting the copilot should use: the live one, otherwise the last one.
    pub fn meeting_for_copilot(&self) -> Option<String> {
        if let Some(id) = self
            .lock()
            .as_ref()
            .and_then(|a| a.uplink.as_ref().map(|u| u.meeting_id.clone()))
        {
            return Some(id);
        }
        self.last_meeting
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .clone()
    }
}

fn native() -> CmdResult<Arc<dyn AudioCaptureProvider>> {
    default_provider().ok_or_else(|| {
        CommandError::new(
            "unsupported",
            "Native audio capture is not available in this build/platform.",
        )
    })
}

#[derive(Debug, Serialize)]
pub struct CapabilitiesDto {
    pub backend: Option<String>,
    pub capabilities: Option<PlatformCapabilities>,
    /// Demo signal (generated tone) is always available for testing the UI pipeline.
    pub demo_available: bool,
}

#[tauri::command]
pub fn get_capabilities() -> CapabilitiesDto {
    match default_provider() {
        Some(p) => CapabilitiesDto {
            backend: Some(p.backend_name()),
            capabilities: Some(p.capabilities()),
            demo_available: true,
        },
        None => CapabilitiesDto {
            backend: None,
            capabilities: None,
            demo_available: true,
        },
    }
}

#[derive(Debug, Serialize)]
pub struct DevicesDto {
    pub inputs: Vec<DeviceInfo>,
    pub outputs: Vec<DeviceInfo>,
}

#[tauri::command]
pub async fn list_devices() -> CmdResult<DevicesDto> {
    // Device enumeration can be slow on some systems; keep it off the main thread.
    tauri::async_runtime::spawn_blocking(|| {
        let p = native()?;
        Ok(DevicesDto {
            inputs: p.list_input_devices()?,
            outputs: p.list_output_devices()?,
        })
    })
    .await
    .map_err(|e| CommandError::new("internal", e.to_string()))?
}

#[tauri::command]
pub async fn request_permission(source: AudioSource) -> CmdResult<PermissionState> {
    // May block while the OS prompt is shown.
    tauri::async_runtime::spawn_blocking(move || Ok(native()?.request_permission(source)))
        .await
        .map_err(|e| CommandError::new("internal", e.to_string()))?
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AutoSuggestions {
    Off,
    Directed,
    All,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Tone {
    Natural,
    Formal,
    Concise,
    Technical,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
pub enum TranslateTo {
    #[serde(rename = "pt-BR")]
    PtBr,
    #[serde(rename = "en")]
    En,
}

/// Live assistance during a transcribed meeting. Validated here so the server only ever
/// receives known values; serialized in the gateway's `copilot.settings` shape.
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct LiveOptions {
    #[serde(rename(serialize = "auto_suggestions"))]
    pub auto_suggestions: AutoSuggestions,
    pub tone: Tone,
    #[serde(rename(serialize = "translate_to"))]
    pub translate_to: Option<TranslateTo>,
}

impl LiveOptions {
    pub fn to_server(self) -> serde_json::Value {
        serde_json::to_value(self).unwrap_or_default()
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TranscriptionRequest {
    /// "pt-BR" | "en" | "multi"
    pub language: String,
    #[serde(default)]
    pub live: Option<LiveOptions>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartRequest {
    pub system_audio: Option<SourceSelection>,
    pub microphone: Option<SourceSelection>,
    /// Use the generated test tone instead of real devices (clearly labelled in the UI).
    #[serde(default)]
    pub demo: bool,
    /// Send audio to the MeetPilot server for live transcription. Off unless the user enabled
    /// it (after giving consent in the UI).
    #[serde(default)]
    pub transcription: Option<TranscriptionRequest>,
}

#[derive(Debug, Clone, Serialize)]
pub struct StatusDto {
    #[serde(flatten)]
    pub status: CaptureStatus,
    pub demo: bool,
    pub meeting_id: Option<String>,
    pub transcribing: bool,
    pub dropped_frames: u64,
}

fn dto(a: &Active, status: CaptureStatus) -> StatusDto {
    StatusDto {
        status,
        demo: a.demo,
        meeting_id: a.uplink.as_ref().map(|u| u.meeting_id.clone()),
        transcribing: a.uplink.as_ref().is_some_and(|u| !u.handle.is_finished()),
        dropped_frames: a.uplink.as_ref().map_or(0, |u| u.handle.dropped_frames()),
    }
}

fn idle_dto() -> StatusDto {
    StatusDto {
        status: CaptureStatus {
            state: SessionState::Idle,
            sources: vec![],
        },
        demo: false,
        meeting_id: None,
        transcribing: false,
        dropped_frames: 0,
    }
}

fn event_handler(
    app: AppHandle,
    uplink: Option<Arc<StreamHandle>>,
) -> meetpilot_audio::EventHandler {
    Arc::new(move |ev| match ev {
        SessionEvent::Level(l) => {
            let _ = app.emit(EVT_LEVEL, l);
        }
        SessionEvent::Status(s) => {
            let _ = app.emit(EVT_STATUS, s);
        }
        SessionEvent::Error(e) => {
            log::warn!("capture error: {e}");
            let _ = app.emit(EVT_ERROR, e);
        }
        // Never logged. Forwarded only when transcription is on; non-blocking.
        SessionEvent::Frame(f) => {
            if let Some(h) = &uplink {
                let source = match f.source {
                    AudioSource::System => Source::System,
                    AudioSource::Microphone => Source::Microphone,
                };
                h.send_frame(source, f.seq as u32, &f.samples);
            }
        }
    })
}

pub fn set_overlay_visible(app: &AppHandle, visible: bool) {
    if let Some(w) = app.get_webview_window(OVERLAY_LABEL) {
        let r = if visible { w.show() } else { w.hide() };
        if let Err(e) = r {
            log::warn!("overlay visibility change failed: {e}");
        }
    }
}

async fn open_uplink(app: &AppHandle, language: &str) -> CmdResult<Uplink> {
    let accounts = app.state::<AccountState>();
    let (client, access) = accounts.access().await?;
    let created = client.create_meeting(&access, language).await?;
    let meeting_id = created.meeting.id.clone();

    let tokens: TokenProvider = {
        let app = app.clone();
        let meeting_id = meeting_id.clone();
        Arc::new(move || {
            let app = app.clone();
            let meeting_id = meeting_id.clone();
            Box::pin(async move {
                let accounts = app.state::<AccountState>();
                let (client, access) = accounts
                    .access()
                    .await
                    .map_err(|_| meetpilot_realtime::ApiError::Unauthorized)?;
                Ok(client
                    .stream_token(&access, &meeting_id)
                    .await?
                    .stream_token)
            })
        })
    };
    let sink: EventSink = {
        let app = app.clone();
        Arc::new(move |e| match e {
            StreamEvent::Status(s) => {
                let _ = app.emit(EVT_LINK, s);
            }
            StreamEvent::Server(v) => {
                let _ = app.emit(EVT_SERVER, v);
            }
        })
    };
    let ws_url = client.ws_url(&created.stream.stream_path);
    let handle = spawn_stream(
        StreamConfig::new(ws_url),
        created.stream.stream_token,
        tokens,
        sink,
    );
    Ok(Uplink {
        handle: Arc::new(handle),
        meeting_id,
    })
}

async fn close_uplink(app: &AppHandle, uplink: Uplink) {
    // Waits for the server to flush final transcripts (bounded by the stream's stop timeout).
    uplink.handle.stop().await;
    match app.state::<AccountState>().access().await {
        Ok((client, access)) => {
            if let Err(e) = client.finish_meeting(&access, &uplink.meeting_id).await {
                log::warn!("could not mark meeting finished: {e}");
            }
        }
        Err(e) => log::warn!("could not mark meeting finished: {}", e.message),
    }
}

/// Start capture. Only ever invoked from an explicit user action in the UI.
#[tauri::command]
pub async fn start_capture(
    app: AppHandle,
    state: State<'_, CaptureState>,
    request: StartRequest,
) -> CmdResult<StatusDto> {
    {
        let guard = state.lock();
        if let Some(a) = guard.as_ref() {
            if a.session.status().state != SessionState::Idle {
                return Err(CommandError::new(
                    "invalid_state",
                    "Capture is already running.",
                ));
            }
        }
    }
    let provider: Arc<dyn AudioCaptureProvider> = if request.demo {
        Arc::new(SyntheticProvider::default())
    } else {
        native()?
    };

    // Connect to the server first so no captured audio is missed.
    let uplink = match &request.transcription {
        Some(t) => {
            let u = open_uplink(&app, &t.language).await?;
            if let Some(live) = t.live {
                u.handle.set_settings(live.to_server());
            }
            Some(u)
        }
        None => None,
    };

    let config = CaptureConfig {
        system_audio: request.system_audio,
        microphone: request.microphone,
    };
    let handler = event_handler(app.clone(), uplink.as_ref().map(|u| Arc::clone(&u.handle)));
    // Opening OS streams can block (permission prompts); do it off the async runtime.
    let (session, result) = tauri::async_runtime::spawn_blocking(move || {
        let session = CaptureSession::new(provider, handler);
        let result = session.start(&config);
        (session, result)
    })
    .await
    .map_err(|e| CommandError::new("internal", e.to_string()))?;

    let status = match result {
        Ok(s) => s,
        Err(e) => {
            if let Some(u) = uplink {
                close_uplink(&app, u).await;
            }
            return Err(e.into());
        }
    };
    if let Some(u) = &uplink {
        *state.last_meeting.lock().unwrap_or_else(|p| p.into_inner()) = Some(u.meeting_id.clone());
    }
    let active = Active {
        session,
        demo: request.demo,
        uplink,
    };
    let out = dto(&active, status);
    *state.lock() = Some(active);
    set_overlay_visible(&app, true);
    Ok(out)
}

fn with_active<T>(
    state: &CaptureState,
    f: impl FnOnce(&Active) -> Result<T, CaptureError>,
) -> CmdResult<T> {
    let guard = state.lock();
    let active = guard
        .as_ref()
        .ok_or_else(|| CommandError::new("invalid_state", "Capture is not running."))?;
    Ok(f(active)?)
}

/// Change live assistance (automatic suggestions, tone, translation) mid-meeting.
#[tauri::command]
pub fn set_live_options(state: State<'_, CaptureState>, options: LiveOptions) -> CmdResult<()> {
    let guard = state.lock();
    let uplink = guard
        .as_ref()
        .and_then(|a| a.uplink.as_ref())
        .ok_or_else(|| CommandError::new("invalid_state", "Transcription is not running."))?;
    uplink.handle.set_settings(options.to_server());
    Ok(())
}

#[tauri::command]
pub fn pause_capture(state: State<'_, CaptureState>) -> CmdResult<StatusDto> {
    with_active(&state, |a| {
        // Local gate first (no frames after this returns), then tell the server.
        let s = a.session.pause()?;
        if let Some(u) = &a.uplink {
            u.handle.pause();
        }
        Ok(dto(a, s))
    })
}

#[tauri::command]
pub fn resume_capture(state: State<'_, CaptureState>) -> CmdResult<StatusDto> {
    with_active(&state, |a| {
        if let Some(u) = &a.uplink {
            u.handle.resume();
        }
        let s = a.session.resume()?;
        Ok(dto(a, s))
    })
}

/// Pause if capturing, resume if paused (global shortcut).
pub fn toggle_pause(state: &CaptureState) -> CmdResult<StatusDto> {
    with_active(state, |a| {
        let status = match a.session.status().state {
            SessionState::Capturing => {
                let s = a.session.pause()?;
                if let Some(u) = &a.uplink {
                    u.handle.pause();
                }
                s
            }
            SessionState::Paused => {
                if let Some(u) = &a.uplink {
                    u.handle.resume();
                }
                a.session.resume()?
            }
            SessionState::Idle => a.session.status(),
        };
        Ok(dto(a, status))
    })
}

/// Stop capture (immediately, locally) and then let the server flush final transcripts.
pub async fn stop_all(app: &AppHandle) -> StatusDto {
    let active = app.state::<CaptureState>().lock().take();
    set_overlay_visible(app, false);
    match active {
        Some(a) => {
            let status = a.session.stop();
            let out = StatusDto {
                status,
                demo: a.demo,
                meeting_id: a.uplink.as_ref().map(|u| u.meeting_id.clone()),
                transcribing: false,
                dropped_frames: a.uplink.as_ref().map_or(0, |u| u.handle.dropped_frames()),
            };
            if let Some(u) = a.uplink {
                close_uplink(app, u).await;
            }
            out
        }
        None => idle_dto(),
    }
}

#[tauri::command]
pub async fn stop_capture(app: AppHandle) -> CmdResult<StatusDto> {
    Ok(stop_all(&app).await)
}

/// Used when the main window closes: stop capture without waiting long on the network.
pub fn stop_for_exit(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::block_on(async move {
        let _ = tokio::time::timeout(Duration::from_secs(3), stop_all(&app)).await;
    });
}

#[tauri::command]
pub fn get_capture_status(state: State<'_, CaptureState>) -> StatusDto {
    match state.lock().as_ref() {
        Some(a) => dto(a, a.session.status()),
        None => idle_dto(),
    }
}

/// Ask the OS to exclude the overlay from screen capture where supported.
///
/// Windows: `SetWindowDisplayAffinity`; macOS: `NSWindow.sharingType = none`. Behaviour varies
/// by OS version and by the capturing app — this is best effort, never a guarantee.
#[tauri::command]
pub fn set_overlay_protection(app: AppHandle, enabled: bool) -> CmdResult<bool> {
    let w = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or_else(|| CommandError::new("not_found", "Overlay window not found."))?;
    w.set_content_protected(enabled)
        .map_err(|e| CommandError::new("window", e.to_string()))?;
    Ok(enabled)
}

#[tauri::command]
pub fn set_overlay_shown(app: AppHandle, visible: bool) {
    set_overlay_visible(&app, visible);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_error_carries_kind_and_user_message() {
        let e: CommandError = CaptureError::PermissionDenied {
            audio: AudioSource::Microphone,
            hint: "Allow it in Settings.".into(),
        }
        .into();
        assert_eq!(e.kind, "permission_denied");
        assert!(e.message.contains("microphone"));
        let v = serde_json::to_value(&e).unwrap();
        assert_eq!(v["detail"]["source"], "microphone");
    }

    #[test]
    fn start_request_defaults_to_no_transcription() {
        let r: StartRequest =
            serde_json::from_str(r#"{"systemAudio":{"device_id":null},"microphone":null}"#)
                .unwrap();
        assert!(r.system_audio.is_some());
        assert!(r.microphone.is_none());
        assert!(!r.demo);
        assert!(
            r.transcription.is_none(),
            "audio never leaves the device by default"
        );
    }

    #[test]
    fn start_request_with_transcription() {
        let r: StartRequest = serde_json::from_str(
            r#"{"systemAudio":null,"microphone":{"device_id":null},"transcription":{"language":"pt-BR"}}"#,
        )
        .unwrap();
        let t = r.transcription.unwrap();
        assert_eq!(t.language, "pt-BR");
        assert!(t.live.is_none());
    }

    #[test]
    fn live_options_are_validated_and_sent_in_server_shape() {
        let o: LiveOptions = serde_json::from_str(
            r#"{"autoSuggestions":"directed","tone":"formal","translateTo":"en"}"#,
        )
        .unwrap();
        assert_eq!(
            o.to_server(),
            serde_json::json!({"auto_suggestions":"directed","tone":"formal","translate_to":"en"})
        );
        let none: LiveOptions = serde_json::from_str(
            r#"{"autoSuggestions":"off","tone":"natural","translateTo":null}"#,
        )
        .unwrap();
        assert!(none.to_server()["translate_to"].is_null());
        assert!(serde_json::from_str::<LiveOptions>(
            r#"{"autoSuggestions":"sometimes","tone":"natural","translateTo":null}"#
        )
        .is_err());
    }

    #[test]
    fn idle_status_serializes_flat() {
        let v = serde_json::to_value(idle_dto()).unwrap();
        assert_eq!(v["state"], "idle");
        assert_eq!(v["transcribing"], false);
        assert!(v["meeting_id"].is_null());
    }

    #[test]
    fn api_errors_become_command_errors() {
        let e: CommandError = meetpilot_realtime::ApiError::QuotaExceeded.into();
        assert_eq!(e.kind, "quota_exceeded");
    }
}
