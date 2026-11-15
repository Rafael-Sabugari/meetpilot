use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::types::AudioSource;

/// Errors surfaced by capture providers and the capture session.
///
/// Messages are written for end users and never include audio content.
#[derive(Debug, Clone, Error, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum CaptureError {
    #[error("Permission to capture {audio} was denied. {hint}")]
    PermissionDenied {
        #[serde(rename = "source")]
        audio: AudioSource,
        hint: String,
    },

    #[error("No audio device was found for {audio}.")]
    DeviceNotFound {
        #[serde(rename = "source")]
        audio: AudioSource,
    },

    #[error("The {audio} device is unavailable or was disconnected: {detail}")]
    DeviceUnavailable {
        #[serde(rename = "source")]
        audio: AudioSource,
        detail: String,
    },

    #[error("{audio} capture is not supported here: {reason}")]
    Unsupported {
        #[serde(rename = "source")]
        audio: AudioSource,
        reason: String,
    },

    #[error("Audio backend error ({audio}): {detail}")]
    Backend {
        #[serde(rename = "source")]
        audio: AudioSource,
        detail: String,
    },

    #[error("Invalid capture configuration: {detail}")]
    InvalidConfig { detail: String },

    #[error("Cannot {action} while the session is {state}.")]
    InvalidState { action: String, state: String },
}

impl CaptureError {
    /// Whether retrying (for example after a device change) can reasonably succeed.
    pub fn is_recoverable(&self) -> bool {
        matches!(
            self,
            CaptureError::DeviceUnavailable { .. }
                | CaptureError::Backend { .. }
                | CaptureError::DeviceNotFound { .. }
        )
    }

    pub fn source(&self) -> Option<AudioSource> {
        match self {
            CaptureError::PermissionDenied { audio: source, .. }
            | CaptureError::DeviceNotFound { audio: source }
            | CaptureError::DeviceUnavailable { audio: source, .. }
            | CaptureError::Unsupported { audio: source, .. }
            | CaptureError::Backend { audio: source, .. } => Some(*source),
            _ => None,
        }
    }
}

pub type Result<T> = std::result::Result<T, CaptureError>;
