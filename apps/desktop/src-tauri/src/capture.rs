//! Tauri commands and events for user-controlled local audio capture.
//!
//! Events emitted to the webviews:
//! * `capture://status` — [`CaptureStatus`] whenever the session or a source changes state.
//! * `capture://level`  — [`LevelReading`] ~10x/s per source (drives the visible indicator).
//! * `capture://error`  — [`CaptureError`] for non-fatal problems (e.g. one source failed).
//!
//! Audio frames are not sent to the UI. In this slice they are counted and discarded; the
//! next slice forwards them to the authenticated realtime gateway for speech-to-text.

use std::sync::{Arc, Mutex};

use meetpilot_audio::providers::synthetic::SyntheticProvider;
use meetpilot_audio::{
    default_provider, AudioCaptureProvider, AudioSource, CaptureConfig, CaptureError,
    CaptureSession, CaptureStatus, DeviceInfo, PermissionState, PlatformCapabilities, SessionEvent,
    SessionState, SourceSelection,
};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

pub const EVT_STATUS: &str = "capture://status";
pub const EVT_LEVEL: &str = "capture://level";
pub const EVT_ERROR: &str = "capture://error";
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
    fn new(kind: &str, message: impl Into<String>) -> Self {
        Self {
            kind: kind.into(),
            message: message.into(),
            detail: None,
        }
    }
}

type CmdResult<T> = Result<T, CommandError>;

struct Active {
    session: CaptureSession,
    demo: bool,
}

#[derive(Default)]
pub struct CaptureState {
    active: Mutex<Option<Active>>,
}

impl CaptureState {
    fn lock(&self) -> std::sync::MutexGuard<'_, Option<Active>> {
        self.active.lock().unwrap_or_else(|p| p.into_inner())
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

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartRequest {
    pub system_audio: Option<SourceSelection>,
    pub microphone: Option<SourceSelection>,
    /// Use the generated test tone instead of real devices (clearly labelled in the UI).
    #[serde(default)]
    pub demo: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct StatusDto {
    #[serde(flatten)]
    pub status: CaptureStatus,
    pub demo: bool,
}

fn event_handler(app: AppHandle) -> meetpilot_audio::EventHandler {
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
        // Not forwarded anywhere yet (no network in this slice). Never logged.
        SessionEvent::Frame(_) => {}
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
    let config = CaptureConfig {
        system_audio: request.system_audio,
        microphone: request.microphone,
    };
    let demo = request.demo;
    let handler = event_handler(app.clone());
    // Opening OS streams can block (permission prompts); do it off the async runtime.
    let (session, result) = tauri::async_runtime::spawn_blocking(move || {
        let session = CaptureSession::new(provider, handler);
        let result = session.start(&config);
        (session, result)
    })
    .await
    .map_err(|e| CommandError::new("internal", e.to_string()))?;
    let status = result?;
    *state.lock() = Some(Active { session, demo });
    set_overlay_visible(&app, true);
    Ok(StatusDto { status, demo })
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

#[tauri::command]
pub fn pause_capture(state: State<'_, CaptureState>) -> CmdResult<StatusDto> {
    with_active(&state, |a| {
        Ok(StatusDto {
            status: a.session.pause()?,
            demo: a.demo,
        })
    })
}

#[tauri::command]
pub fn resume_capture(state: State<'_, CaptureState>) -> CmdResult<StatusDto> {
    with_active(&state, |a| {
        Ok(StatusDto {
            status: a.session.resume()?,
            demo: a.demo,
        })
    })
}

/// Pause if capturing, resume if paused (global shortcut).
pub fn toggle_pause(state: &CaptureState) -> CmdResult<StatusDto> {
    with_active(state, |a| {
        let status = match a.session.status().state {
            SessionState::Capturing => a.session.pause()?,
            SessionState::Paused => a.session.resume()?,
            SessionState::Idle => a.session.status(),
        };
        Ok(StatusDto {
            status,
            demo: a.demo,
        })
    })
}

#[tauri::command]
pub fn stop_capture(app: AppHandle, state: State<'_, CaptureState>) -> CmdResult<StatusDto> {
    let active = state.lock().take();
    let dto = match active {
        Some(a) => StatusDto {
            status: a.session.stop(),
            demo: a.demo,
        },
        None => StatusDto {
            status: CaptureStatus {
                state: SessionState::Idle,
                sources: vec![],
            },
            demo: false,
        },
    };
    set_overlay_visible(&app, false);
    Ok(dto)
}

#[tauri::command]
pub fn get_capture_status(state: State<'_, CaptureState>) -> StatusDto {
    match state.lock().as_ref() {
        Some(a) => StatusDto {
            status: a.session.status(),
            demo: a.demo,
        },
        None => StatusDto {
            status: CaptureStatus {
                state: SessionState::Idle,
                sources: vec![],
            },
            demo: false,
        },
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
    fn start_request_accepts_camel_case_and_defaults_demo() {
        let r: StartRequest =
            serde_json::from_str(r#"{"systemAudio":{"device_id":null},"microphone":null}"#)
                .unwrap();
        assert!(r.system_audio.is_some());
        assert!(r.microphone.is_none());
        assert!(!r.demo);
    }

    #[test]
    fn status_dto_flattens_status() {
        let dto = StatusDto {
            status: CaptureStatus {
                state: SessionState::Paused,
                sources: vec![],
            },
            demo: true,
        };
        let v = serde_json::to_value(&dto).unwrap();
        assert_eq!(v["state"], "paused");
        assert_eq!(v["demo"], true);
    }
}
