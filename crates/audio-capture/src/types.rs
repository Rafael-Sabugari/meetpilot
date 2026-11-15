use serde::{Deserialize, Serialize};
use std::fmt;

/// Which audio the user chose to capture.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioSource {
    /// Audio played by the computer (remote meeting participants), via OS loopback.
    System,
    /// The user's own microphone.
    Microphone,
}

impl fmt::Display for AudioSource {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AudioSource::System => f.write_str("system audio"),
            AudioSource::Microphone => f.write_str("microphone"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceDirection {
    Input,
    Output,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceInfo {
    /// Stable backend identifier, passed back in [`SourceSelection::device_id`].
    pub id: String,
    pub name: String,
    pub direction: DeviceDirection,
    pub is_default: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionState {
    Granted,
    Denied,
    /// The OS will prompt the user on first use.
    NotDetermined,
    /// No OS permission is required for this source on this platform.
    NotRequired,
    /// The OS offers no API to query the state ahead of time.
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum SupportLevel {
    Supported { backend: String },
    Unsupported { reason: String },
}

impl SupportLevel {
    pub fn is_supported(&self) -> bool {
        matches!(self, SupportLevel::Supported { .. })
    }
}

/// What the current machine can do, shown during onboarding and in the test screen.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlatformCapabilities {
    pub os: String,
    pub os_version: Option<String>,
    pub system_audio: SupportLevel,
    pub microphone: SupportLevel,
    pub system_audio_permission: PermissionState,
    pub microphone_permission: PermissionState,
    /// Human-readable notes (OS requirements, known limitations).
    pub notes: Vec<String>,
}

/// Format delivered by the OS before normalization.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct NativeFormat {
    pub sample_rate: u32,
    pub channels: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct SourceSelection {
    /// `None` uses the OS default device (and follows it where the backend supports that).
    pub device_id: Option<String>,
}

/// Internal PCM format sent downstream (speech-to-text friendly).
pub const TARGET_SAMPLE_RATE: u32 = 16_000;
/// 20 ms frames: a common STT streaming chunk size.
pub const FRAME_DURATION_MS: u32 = 20;
pub const SAMPLES_PER_FRAME: usize = (TARGET_SAMPLE_RATE * FRAME_DURATION_MS / 1000) as usize;

#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct CaptureConfig {
    /// Capture computer output (remote participants). `None` disables it.
    pub system_audio: Option<SourceSelection>,
    /// Capture the user's microphone. `None` disables it.
    pub microphone: Option<SourceSelection>,
}

impl CaptureConfig {
    pub fn sources(&self) -> Vec<(AudioSource, SourceSelection)> {
        let mut v = Vec::new();
        if let Some(s) = &self.system_audio {
            v.push((AudioSource::System, s.clone()));
        }
        if let Some(s) = &self.microphone {
            v.push((AudioSource::Microphone, s.clone()));
        }
        v
    }
}

/// A normalized frame: 16 kHz, mono, signed 16-bit little-endian PCM, 20 ms.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PcmFrame {
    pub source: AudioSource,
    /// Monotonic per-source sequence number (gaps are never silently hidden: see `gap_before`).
    pub seq: u64,
    /// Milliseconds of captured audio for this source before this frame.
    pub offset_ms: u64,
    /// True if audio was dropped or the stream reconnected right before this frame.
    pub gap_before: bool,
    pub samples: Vec<i16>,
}

impl PcmFrame {
    pub fn to_le_bytes(&self) -> Vec<u8> {
        self.samples.iter().flat_map(|s| s.to_le_bytes()).collect()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct LevelReading {
    pub source: AudioSource,
    /// RMS level in dBFS (-100 for silence).
    pub rms_dbfs: f32,
    /// Peak absolute sample value, 0.0..=1.0.
    pub peak: f32,
}
