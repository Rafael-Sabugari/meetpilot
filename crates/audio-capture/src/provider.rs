//! The platform-independent capture abstraction.
//!
//! A provider opens OS streams; each stream pushes raw interleaved samples into a
//! [`RawAudioSink`]. The sink never blocks the real-time audio callback: when the
//! downstream queue is full, the chunk is dropped and counted so the gap can be
//! surfaced to the user instead of silently lost.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::error::{CaptureError, Result};
use crate::types::{
    AudioSource, DeviceInfo, NativeFormat, PermissionState, PlatformCapabilities, SourceSelection,
};

/// Messages flowing from an OS stream to its session worker.
#[derive(Debug)]
pub(crate) enum RawMsg {
    Audio {
        samples: Vec<f32>,
        format: NativeFormat,
    },
    Error(CaptureError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SendOutcome {
    Sent,
    Full,
    Closed,
}

/// Non-blocking send into the worker queue.
pub(crate) type SendFn = Arc<dyn Fn(RawMsg) -> SendOutcome + Send + Sync>;
/// Fallback slot for an error that could not be queued (queue full); polled by the worker.
pub(crate) type PendingError = Arc<Mutex<Option<CaptureError>>>;

/// Handed to backends; cheap to clone and safe to call from real-time audio threads.
#[derive(Clone)]
pub struct RawAudioSink {
    send: SendFn,
    pending_error: PendingError,
    gate_closed: Arc<AtomicBool>,
    dropped_chunks: Arc<AtomicU64>,
    source: AudioSource,
}

impl RawAudioSink {
    pub(crate) fn new(
        send: SendFn,
        pending_error: PendingError,
        gate_closed: Arc<AtomicBool>,
        dropped_chunks: Arc<AtomicU64>,
        source: AudioSource,
    ) -> Self {
        Self {
            send,
            pending_error,
            gate_closed,
            dropped_chunks,
            source,
        }
    }

    pub fn source(&self) -> AudioSource {
        self.source
    }

    /// True while the user has paused capture. Backends may use this to skip work.
    pub fn is_paused(&self) -> bool {
        self.gate_closed.load(Ordering::Acquire)
    }

    /// Push interleaved f32 samples in `[-1.0, 1.0]`. Never blocks.
    pub fn push_f32(&self, interleaved: &[f32], format: NativeFormat) {
        if interleaved.is_empty() || self.is_paused() {
            return;
        }
        self.send_audio(interleaved.to_vec(), format);
    }

    /// Push with a conversion closure (avoids an intermediate buffer for non-f32 formats).
    pub fn push_with<T: Copy>(
        &self,
        interleaved: &[T],
        format: NativeFormat,
        f: impl Fn(T) -> f32,
    ) {
        if interleaved.is_empty() || self.is_paused() {
            return;
        }
        self.send_audio(interleaved.iter().map(|s| f(*s)).collect(), format);
    }

    fn send_audio(&self, samples: Vec<f32>, format: NativeFormat) {
        if (self.send)(RawMsg::Audio { samples, format }) == SendOutcome::Full {
            self.dropped_chunks.fetch_add(1, Ordering::Relaxed);
        }
    }

    /// Report an asynchronous stream failure (device unplugged, service restarted, ...).
    pub fn report_error(&self, err: CaptureError) {
        // Never block (this may run on a real-time thread). If the queue is full, park the
        // error in a slot the worker polls, so it is not lost.
        if (self.send)(RawMsg::Error(err.clone())) != SendOutcome::Sent {
            if let Ok(mut slot) = self.pending_error.lock() {
                slot.get_or_insert(err);
            }
        }
    }
}

/// A running OS stream. Dropping it must stop the stream and release OS resources.
///
/// Streams are created and owned by the session worker thread, so they need not be `Send`.
pub trait CaptureStream {
    fn native_format(&self) -> NativeFormat;
    fn device_name(&self) -> String;
    /// Pause at the OS level if supported. The session additionally gates audio itself,
    /// so returning `Ok(())` without doing anything is acceptable.
    fn pause(&mut self) -> Result<()>;
    fn resume(&mut self) -> Result<()>;
}

/// Platform capture backend (see `docs/ARCHITECTURE.md`, "Audio capture abstraction").
pub trait AudioCaptureProvider: Send + Sync {
    fn backend_name(&self) -> String;
    fn capabilities(&self) -> PlatformCapabilities;
    fn list_input_devices(&self) -> Result<Vec<DeviceInfo>>;
    fn list_output_devices(&self) -> Result<Vec<DeviceInfo>>;
    fn permission_status(&self, source: AudioSource) -> PermissionState;
    /// Trigger the OS prompt where one exists. Returns the resulting state when known.
    fn request_permission(&self, source: AudioSource) -> PermissionState;
    fn open(
        &self,
        source: AudioSource,
        selection: &SourceSelection,
        sink: RawAudioSink,
    ) -> Result<Box<dyn CaptureStream>>;
}
