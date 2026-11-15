//! MeetPilot local audio capture.
//!
//! Captures audio that is already playing on the user's own computer (OS loopback) and,
//! optionally, the user's microphone — only after explicit user action. MeetPilot never
//! joins a meeting as a bot or participant; this crate has no network code at all.
//!
//! Output: 16 kHz mono PCM16 frames of 20 ms, tagged by source, plus level readings and
//! status events for a visible capture indicator.

pub mod dsp;
pub mod error;
pub mod platform;
pub mod provider;
pub mod providers;
pub mod session;
pub mod types;

pub use error::{CaptureError, Result};
pub use platform::default_provider;
pub use provider::{AudioCaptureProvider, CaptureStream, RawAudioSink};
pub use session::{
    CaptureSession, CaptureStatus, EventHandler, ReconnectPolicy, SessionEvent, SessionOptions,
    SessionState, SourceState, SourceStatus,
};
pub use types::*;
