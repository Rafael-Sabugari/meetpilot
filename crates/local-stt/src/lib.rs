//! MeetPilot private mode: on-device speech-to-text with Whisper.
//!
//! Audio handled here never leaves the computer. The engine decides when to transcribe
//! (voice activity), runs the model on a background thread, and emits transcript segments
//! in the same shape as the cloud path so the UI treats both alike.

pub mod engine;
pub mod models;
pub mod segmenter;
pub mod text;
#[cfg(feature = "whisper")]
pub mod whisper;

pub use engine::{
    EngineConfig, EventSink, LocalEvent, LocalSegment, LocalTranscriber, Recognizer,
    RecognizerFactory, Source,
};
pub use models::{recommended_model, spec, ModelError, ModelInfo, ModelSpec, ModelStore, MODELS};
