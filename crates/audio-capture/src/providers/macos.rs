//! macOS provider: picks the system-audio backend by OS version and handles permissions.
//!
//! | macOS        | System audio backend                     | Permission                         |
//! |--------------|------------------------------------------|------------------------------------|
//! | 14.6+        | CoreAudio process tap (via cpal)         | System Audio Recording (audio only)|
//! | 13.0 – 14.5  | ScreenCaptureKit (audio-only stream)     | Screen & System Audio Recording    |
//! | < 13         | unsupported                              | –                                  |
//!
//! The process tap is preferred because it does not require screen-recording permission.
//! Set `MEETPILOT_MACOS_SYSTEM_BACKEND=sck` or `=tap` to force a backend for testing.

use std::sync::mpsc;
use std::time::Duration;

use block2::RcBlock;
use objc2::runtime::Bool;
use objc2_av_foundation::{AVAuthorizationStatus, AVCaptureDevice, AVMediaTypeAudio};
use objc2_foundation::NSProcessInfo;

use crate::error::{CaptureError, Result};
use crate::provider::{AudioCaptureProvider, CaptureStream, RawAudioSink};
use crate::providers::cpal_backend::CpalProvider;
use crate::providers::screencapturekit;
use crate::types::{
    AudioSource, DeviceInfo, PermissionState, PlatformCapabilities, SourceSelection, SupportLevel,
};

pub use crate::platform::{choose_macos_backend as choose_backend, MacSystemBackend};

pub fn os_version() -> (isize, isize, isize) {
    let v = NSProcessInfo::processInfo().operatingSystemVersion();
    (v.majorVersion, v.minorVersion, v.patchVersion)
}

pub struct MacProvider {
    cpal: CpalProvider,
    backend: MacSystemBackend,
    version: (isize, isize, isize),
}

impl MacProvider {
    pub fn new() -> Self {
        let version = os_version();
        let forced = std::env::var("MEETPILOT_MACOS_SYSTEM_BACKEND").ok();
        let backend = choose_backend(version, forced.as_deref());
        let support = match backend {
            MacSystemBackend::ProcessTap => SupportLevel::Supported {
                backend: "CoreAudio process tap".into(),
            },
            MacSystemBackend::ScreenCaptureKit => SupportLevel::Supported {
                backend: "ScreenCaptureKit".into(),
            },
            MacSystemBackend::Unsupported => SupportLevel::Unsupported {
                reason: "System audio capture requires macOS 13 or later.".into(),
            },
        };
        Self {
            cpal: CpalProvider::new(support),
            backend,
            version,
        }
    }

    pub fn system_backend(&self) -> MacSystemBackend {
        self.backend
    }

    fn mic_status() -> PermissionState {
        let Some(media) = (unsafe { AVMediaTypeAudio }) else {
            return PermissionState::Unknown;
        };
        let status = unsafe { AVCaptureDevice::authorizationStatusForMediaType(media) };
        match status {
            AVAuthorizationStatus::Authorized => PermissionState::Granted,
            AVAuthorizationStatus::Denied | AVAuthorizationStatus::Restricted => {
                PermissionState::Denied
            }
            AVAuthorizationStatus::NotDetermined => PermissionState::NotDetermined,
            _ => PermissionState::Unknown,
        }
    }

    fn request_mic() -> PermissionState {
        let Some(media) = (unsafe { AVMediaTypeAudio }) else {
            return PermissionState::Unknown;
        };
        let (tx, rx) = mpsc::sync_channel::<bool>(1);
        let block = RcBlock::new(move |granted: Bool| {
            let _ = tx.try_send(granted.as_bool());
        });
        unsafe { AVCaptureDevice::requestAccessForMediaType_completionHandler(media, &block) };
        match rx.recv_timeout(Duration::from_secs(120)) {
            Ok(true) => PermissionState::Granted,
            Ok(false) => PermissionState::Denied,
            Err(_) => Self::mic_status(),
        }
    }
}

impl Default for MacProvider {
    fn default() -> Self {
        Self::new()
    }
}

impl AudioCaptureProvider for MacProvider {
    fn backend_name(&self) -> String {
        format!("macos/{:?}", self.backend)
    }

    fn capabilities(&self) -> PlatformCapabilities {
        let mut caps = self.cpal.capabilities();
        let (a, b, c) = self.version;
        caps.os_version = Some(format!("{a}.{b}.{c}"));
        caps.system_audio_permission = self.permission_status(AudioSource::System);
        caps.microphone_permission = self.permission_status(AudioSource::Microphone);
        caps.notes = match self.backend {
            MacSystemBackend::ProcessTap => vec![
                "System audio uses a CoreAudio process tap (macOS 14.6+). macOS asks for \"System Audio Recording\" permission the first time capture starts.".into(),
                "No screen content is captured.".into(),
            ],
            MacSystemBackend::ScreenCaptureKit => vec![
                "System audio uses ScreenCaptureKit (macOS 13 – 14.5). macOS requires the \"Screen & System Audio Recording\" permission; MeetPilot requests audio only and discards the minimal video frames.".into(),
                "After granting the permission, macOS may require restarting MeetPilot.".into(),
            ],
            MacSystemBackend::Unsupported => vec!["System audio capture requires macOS 13 or later. Microphone capture still works.".into()],
        };
        caps
    }

    fn list_input_devices(&self) -> Result<Vec<DeviceInfo>> {
        self.cpal.list_input_devices()
    }

    fn list_output_devices(&self) -> Result<Vec<DeviceInfo>> {
        self.cpal.list_output_devices()
    }

    fn permission_status(&self, source: AudioSource) -> PermissionState {
        match source {
            AudioSource::Microphone => Self::mic_status(),
            AudioSource::System => match self.backend {
                // No public preflight API for the audio-capture TCC service.
                MacSystemBackend::ProcessTap => PermissionState::Unknown,
                MacSystemBackend::ScreenCaptureKit => screencapturekit::preflight(),
                MacSystemBackend::Unsupported => PermissionState::Unknown,
            },
        }
    }

    fn request_permission(&self, source: AudioSource) -> PermissionState {
        match source {
            AudioSource::Microphone => Self::request_mic(),
            AudioSource::System => match self.backend {
                MacSystemBackend::ScreenCaptureKit => screencapturekit::request(),
                _ => self.permission_status(source),
            },
        }
    }

    fn open(
        &self,
        source: AudioSource,
        selection: &SourceSelection,
        sink: RawAudioSink,
    ) -> Result<Box<dyn CaptureStream>> {
        match (source, self.backend) {
            (AudioSource::Microphone, _) => self.cpal.open(source, selection, sink),
            (AudioSource::System, MacSystemBackend::ProcessTap) => {
                self.cpal.open(source, selection, sink)
            }
            (AudioSource::System, MacSystemBackend::ScreenCaptureKit) => Ok(Box::new(
                screencapturekit::ScreenCaptureKitStream::start(sink)?,
            )),
            (AudioSource::System, MacSystemBackend::Unsupported) => {
                Err(CaptureError::Unsupported {
                    audio: source,
                    reason: "System audio capture requires macOS 13 or later.".into(),
                })
            }
        }
    }

    fn route_signature(&self, source: AudioSource, selection: &SourceSelection) -> Option<String> {
        match (source, self.backend) {
            (AudioSource::Microphone, _) | (AudioSource::System, MacSystemBackend::ProcessTap) => {
                self.cpal.route_signature(source, selection)
            }
            _ => None,
        }
    }
}
