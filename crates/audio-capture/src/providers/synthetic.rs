//! Deterministic signal generator implementing [`AudioCaptureProvider`].
//!
//! Used by automated tests and as an explicitly labelled "demo signal" in the desktop
//! app on platforms without native capture. It never touches real audio devices.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use crate::error::{CaptureError, Result};
use crate::provider::{AudioCaptureProvider, CaptureStream, RawAudioSink};
use crate::types::{
    AudioSource, DeviceDirection, DeviceInfo, NativeFormat, PermissionState, PlatformCapabilities,
    SourceSelection, SupportLevel,
};

#[derive(Debug, Clone)]
pub struct SyntheticConfig {
    pub format: NativeFormat,
    pub tone_hz: f32,
    pub amplitude: f32,
    pub chunk_ms: u32,
    /// Sleep `chunk_ms` between chunks (true) or push as fast as possible (false).
    pub realtime: bool,
}

impl Default for SyntheticConfig {
    fn default() -> Self {
        Self {
            format: NativeFormat {
                sample_rate: 48_000,
                channels: 2,
            },
            tone_hz: 440.0,
            amplitude: 0.25,
            chunk_ms: 10,
            realtime: true,
        }
    }
}

/// Failure injection for tests.
#[derive(Debug, Default)]
pub struct FaultPlan {
    /// Number of upcoming `open` calls that fail, per source.
    pub fail_opens_system: AtomicU32,
    pub fail_opens_mic: AtomicU32,
    pub open_error: Mutex<Option<CaptureError>>,
    /// The first opened stream reports `DeviceUnavailable` after this many chunks (0 = never).
    pub disconnect_after_chunks: AtomicU32,
    /// With `disconnect_after_chunks`: the stream just stops sending, without an error
    /// (like a Bluetooth device switching profile).
    pub silent_disconnect: AtomicBool,
    /// The next `resume()` on any stream fails with a backend error.
    pub fail_next_resume: AtomicBool,
}

pub struct SyntheticProvider {
    pub config: SyntheticConfig,
    pub faults: Arc<FaultPlan>,
    disconnect_used: Arc<AtomicBool>,
}

impl SyntheticProvider {
    pub fn new(config: SyntheticConfig) -> Self {
        Self {
            config,
            faults: Arc::new(FaultPlan::default()),
            disconnect_used: Arc::new(AtomicBool::new(false)),
        }
    }
}

impl Default for SyntheticProvider {
    fn default() -> Self {
        Self::new(SyntheticConfig::default())
    }
}

impl AudioCaptureProvider for SyntheticProvider {
    fn backend_name(&self) -> String {
        "synthetic".into()
    }

    fn capabilities(&self) -> PlatformCapabilities {
        let s = SupportLevel::Supported {
            backend: "synthetic test signal".into(),
        };
        PlatformCapabilities {
            os: std::env::consts::OS.into(),
            os_version: None,
            system_audio: s.clone(),
            microphone: s,
            system_audio_permission: PermissionState::NotRequired,
            microphone_permission: PermissionState::NotRequired,
            notes: vec!["Demo mode: a generated tone, not real audio.".into()],
        }
    }

    fn list_input_devices(&self) -> Result<Vec<DeviceInfo>> {
        Ok(vec![DeviceInfo {
            id: "synthetic:mic".into(),
            name: "Synthetic microphone".into(),
            direction: DeviceDirection::Input,
            is_default: true,
        }])
    }

    fn list_output_devices(&self) -> Result<Vec<DeviceInfo>> {
        Ok(vec![DeviceInfo {
            id: "synthetic:out".into(),
            name: "Synthetic output".into(),
            direction: DeviceDirection::Output,
            is_default: true,
        }])
    }

    fn permission_status(&self, _source: AudioSource) -> PermissionState {
        PermissionState::NotRequired
    }

    fn request_permission(&self, _source: AudioSource) -> PermissionState {
        PermissionState::NotRequired
    }

    fn open(
        &self,
        source: AudioSource,
        _selection: &SourceSelection,
        sink: RawAudioSink,
    ) -> Result<Box<dyn CaptureStream>> {
        let counter = match source {
            AudioSource::System => &self.faults.fail_opens_system,
            AudioSource::Microphone => &self.faults.fail_opens_mic,
        };
        if take_one(counter) {
            let err = self.faults.open_error.lock().unwrap().clone().unwrap_or(
                CaptureError::DeviceUnavailable {
                    audio: source,
                    detail: "injected failure".into(),
                },
            );
            return Err(err);
        }

        let disconnect_after = if self.disconnect_used.swap(true, Ordering::SeqCst) {
            0
        } else {
            self.faults.disconnect_after_chunks.load(Ordering::SeqCst)
        };
        let silent = self.faults.silent_disconnect.load(Ordering::SeqCst);
        let mut stream =
            SyntheticStream::spawn(self.config.clone(), source, sink, disconnect_after, silent);
        stream.faults = Some(Arc::clone(&self.faults));
        Ok(Box::new(stream))
    }
}

/// Atomically decrement `counter` if it is positive. Returns whether one was taken.
fn take_one(counter: &AtomicU32) -> bool {
    let mut cur = counter.load(Ordering::SeqCst);
    while cur > 0 {
        match counter.compare_exchange(cur, cur - 1, Ordering::SeqCst, Ordering::SeqCst) {
            Ok(_) => return true,
            Err(actual) => cur = actual,
        }
    }
    false
}

struct SyntheticStream {
    format: NativeFormat,
    running: Arc<AtomicBool>,
    os_paused: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
    faults: Option<Arc<FaultPlan>>,
    source: AudioSource,
}

impl SyntheticStream {
    fn spawn(
        cfg: SyntheticConfig,
        source: AudioSource,
        sink: RawAudioSink,
        disconnect_after: u32,
        silent: bool,
    ) -> Self {
        let running = Arc::new(AtomicBool::new(true));
        let os_paused = Arc::new(AtomicBool::new(false));
        let (r, p) = (Arc::clone(&running), Arc::clone(&os_paused));
        let format = cfg.format;
        let join = std::thread::spawn(move || {
            let ch = format.channels.max(1) as usize;
            let frames = (format.sample_rate * cfg.chunk_ms / 1000) as usize;
            let mut phase = 0.0f32;
            let dphi = 2.0 * std::f32::consts::PI * cfg.tone_hz / format.sample_rate as f32;
            let mut sent = 0u32;
            let mut buf = vec![0.0f32; frames * ch];
            while r.load(Ordering::SeqCst) {
                if !p.load(Ordering::SeqCst) {
                    for f in 0..frames {
                        let v = cfg.amplitude * phase.sin();
                        phase = (phase + dphi) % (2.0 * std::f32::consts::PI);
                        buf[f * ch..(f + 1) * ch].fill(v);
                    }
                    sink.push_f32(&buf, format);
                    sent += 1;
                    if disconnect_after > 0 && sent == disconnect_after {
                        if silent {
                            return;
                        }
                        sink.report_error(CaptureError::DeviceUnavailable {
                            audio: source,
                            detail: "synthetic device unplugged".into(),
                        });
                        return;
                    }
                }
                if cfg.realtime {
                    std::thread::sleep(Duration::from_millis(cfg.chunk_ms as u64));
                } else {
                    std::thread::yield_now();
                }
            }
        });
        Self {
            format,
            running,
            os_paused,
            join: Some(join),
            faults: None,
            source,
        }
    }
}

impl CaptureStream for SyntheticStream {
    fn native_format(&self) -> NativeFormat {
        self.format
    }
    fn device_name(&self) -> String {
        "Synthetic tone".into()
    }
    fn pause(&mut self) -> Result<()> {
        self.os_paused.store(true, Ordering::SeqCst);
        Ok(())
    }
    fn resume(&mut self) -> Result<()> {
        if let Some(f) = &self.faults {
            if f.fail_next_resume.swap(false, Ordering::SeqCst) {
                return Err(CaptureError::Backend {
                    audio: self.source,
                    detail: "injected resume failure".into(),
                });
            }
        }
        self.os_paused.store(false, Ordering::SeqCst);
        Ok(())
    }
}

impl Drop for SyntheticStream {
    fn drop(&mut self) {
        self.running.store(false, Ordering::SeqCst);
        if let Some(j) = self.join.take() {
            let _ = j.join();
        }
    }
}
