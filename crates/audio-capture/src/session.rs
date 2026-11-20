//! User-controlled capture session: start / pause / resume / stop across sources.
//!
//! Guarantees (covered by `tests/session.rs`):
//! * After [`CaptureSession::pause`] returns, no [`SessionEvent::Frame`] is emitted until
//!   [`CaptureSession::resume`]; audio queued before the pause is discarded, not sent.
//! * The real-time audio callback never blocks; overflow is counted and surfaced as a gap.
//! * Recoverable stream failures (device unplugged, endpoint changed) trigger bounded
//!   automatic reconnection with visible `Reconnecting` status.
//! * Raw audio is never written to disk by this module.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::dsp::{downmix_to_mono, f32_to_i16, levels, Framer, StreamingResampler};
use crate::error::{CaptureError, Result};
use crate::provider::{
    AudioCaptureProvider, CaptureStream, PendingError, RawAudioSink, RawMsg, SendFn, SendOutcome,
};
use crate::types::{
    AudioSource, CaptureConfig, LevelReading, NativeFormat, PcmFrame, SourceSelection,
    FRAME_DURATION_MS, TARGET_SAMPLE_RATE,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionState {
    Idle,
    Capturing,
    Paused,
}

impl SessionState {
    fn label(self) -> &'static str {
        match self {
            SessionState::Idle => "idle",
            SessionState::Capturing => "capturing",
            SessionState::Paused => "paused",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum SourceState {
    Starting,
    Capturing,
    Paused,
    Reconnecting { attempt: u32 },
    Failed { error: CaptureError },
    Stopped,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceStatus {
    pub source: AudioSource,
    #[serde(flatten)]
    pub state: SourceState,
    pub device_name: Option<String>,
    pub native_format: Option<NativeFormat>,
    pub frames_emitted: u64,
    pub captured_ms: u64,
    pub dropped_chunks: u64,
    pub reconnects: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CaptureStatus {
    pub state: SessionState,
    pub sources: Vec<SourceStatus>,
}

#[derive(Debug, Clone)]
pub enum SessionEvent {
    Frame(PcmFrame),
    Level(LevelReading),
    Status(CaptureStatus),
    Error(CaptureError),
}

pub type EventHandler = Arc<dyn Fn(SessionEvent) + Send + Sync>;

#[derive(Debug, Clone)]
pub struct ReconnectPolicy {
    pub max_attempts: u32,
    pub backoff: Duration,
}

#[derive(Debug, Clone)]
pub struct SessionOptions {
    /// Bounded queue between the OS callback and the processing worker (in callback chunks).
    pub raw_queue_chunks: usize,
    pub level_interval: Duration,
    pub reconnect: ReconnectPolicy,
    pub open_timeout: Duration,
    pub control_timeout: Duration,
    /// Reopen the stream when no audio callback arrives for this long while capturing.
    /// Catches devices that stop silently, e.g. AirPods switching to headset mode when a
    /// meeting app turns on their microphone. `None` disables the watchdog.
    pub stall_timeout: Option<Duration>,
}

impl Default for SessionOptions {
    fn default() -> Self {
        Self {
            raw_queue_chunks: 128,
            level_interval: Duration::from_millis(100),
            reconnect: ReconnectPolicy {
                max_attempts: 10,
                backoff: Duration::from_secs(1),
            },
            open_timeout: Duration::from_secs(15),
            control_timeout: Duration::from_secs(2),
            stall_timeout: Some(Duration::from_secs(4)),
        }
    }
}

enum Control {
    Pause(SyncSender<()>),
    Resume(SyncSender<()>),
    Stop,
}

enum WorkerMsg {
    Raw(RawMsg),
    Control(Control),
}

struct Worker {
    tx: SyncSender<WorkerMsg>,
    join: Option<JoinHandle<()>>,
}

struct Shared {
    status: Mutex<CaptureStatus>,
    handler: EventHandler,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, CaptureStatus> {
        self.status.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn update_source(&self, idx: usize, f: impl FnOnce(&mut SourceStatus)) -> CaptureStatus {
        let mut st = self.lock();
        if let Some(s) = st.sources.get_mut(idx) {
            f(s);
        }
        st.clone()
    }

    fn emit_status(&self, status: CaptureStatus) {
        (self.handler)(SessionEvent::Status(status));
    }
}

pub struct CaptureSession {
    provider: Arc<dyn AudioCaptureProvider>,
    options: SessionOptions,
    shared: Arc<Shared>,
    /// Gate shared with every sink: true = paused (audio is rejected at the callback).
    gate_closed: Arc<AtomicBool>,
    workers: Mutex<Vec<Worker>>,
}

impl CaptureSession {
    pub fn new(provider: Arc<dyn AudioCaptureProvider>, handler: EventHandler) -> Self {
        Self::with_options(provider, handler, SessionOptions::default())
    }

    pub fn with_options(
        provider: Arc<dyn AudioCaptureProvider>,
        handler: EventHandler,
        options: SessionOptions,
    ) -> Self {
        Self {
            provider,
            options,
            shared: Arc::new(Shared {
                status: Mutex::new(CaptureStatus {
                    state: SessionState::Idle,
                    sources: Vec::new(),
                }),
                handler,
            }),
            gate_closed: Arc::new(AtomicBool::new(false)),
            workers: Mutex::new(Vec::new()),
        }
    }

    pub fn provider(&self) -> &Arc<dyn AudioCaptureProvider> {
        &self.provider
    }

    pub fn status(&self) -> CaptureStatus {
        self.shared.lock().clone()
    }

    fn workers(&self) -> MutexGuard<'_, Vec<Worker>> {
        self.workers.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn invalid(&self, action: &str) -> CaptureError {
        CaptureError::InvalidState {
            action: action.into(),
            state: self.status().state.label().into(),
        }
    }

    /// Start capturing. Requires explicit user action in the UI; never called automatically.
    ///
    /// Succeeds if at least one source opened; sources that failed are reported as `Failed`.
    pub fn start(&self, config: &CaptureConfig) -> Result<CaptureStatus> {
        let mut workers = self.workers();
        if !workers.is_empty() {
            return Err(self.invalid("start"));
        }
        let sources = config.sources();
        if sources.is_empty() {
            return Err(CaptureError::InvalidConfig {
                detail: "select at least one audio source".into(),
            });
        }

        self.gate_closed.store(false, Ordering::SeqCst);
        {
            let mut st = self.shared.lock();
            st.state = SessionState::Capturing;
            st.sources = sources
                .iter()
                .map(|(source, _)| SourceStatus {
                    source: *source,
                    state: SourceState::Starting,
                    device_name: None,
                    native_format: None,
                    frames_emitted: 0,
                    captured_ms: 0,
                    dropped_chunks: 0,
                    reconnects: 0,
                })
                .collect();
        }

        let mut open_results = Vec::new();
        for (idx, (source, selection)) in sources.into_iter().enumerate() {
            let (tx, rx) = mpsc::sync_channel::<WorkerMsg>(self.options.raw_queue_chunks.max(1));
            let (open_tx, open_rx) = mpsc::sync_channel::<Result<()>>(1);
            let ctx = WorkerCtx {
                idx,
                source,
                selection,
                provider: Arc::clone(&self.provider),
                shared: Arc::clone(&self.shared),
                gate_closed: Arc::clone(&self.gate_closed),
                options: self.options.clone(),
                tx: tx.clone(),
            };
            let join = std::thread::Builder::new()
                .name(format!("mp-capture-{source:?}").to_lowercase())
                .spawn(move || ctx.run(rx, open_tx))
                .map_err(|e| CaptureError::Backend {
                    audio: source,
                    detail: format!("failed to spawn capture worker: {e}"),
                })?;
            workers.push(Worker {
                tx,
                join: Some(join),
            });
            open_results.push((source, open_rx));
        }

        let deadline = Instant::now() + self.options.open_timeout;
        let mut errors = Vec::new();
        for (source, rx) in open_results {
            let wait = deadline.saturating_duration_since(Instant::now());
            match rx.recv_timeout(wait) {
                Ok(Ok(())) => {}
                Ok(Err(e)) => errors.push(e),
                Err(_) => errors.push(CaptureError::Backend {
                    audio: source,
                    detail: "timed out opening the audio stream".into(),
                }),
            }
        }

        if errors.len() == workers.len() {
            Self::shutdown_workers(&mut workers);
            let mut st = self.shared.lock();
            st.state = SessionState::Idle;
            let snapshot = st.clone();
            drop(st);
            self.shared.emit_status(snapshot);
            return Err(errors.remove(0));
        }
        for e in errors {
            (self.shared.handler)(SessionEvent::Error(e));
        }
        let status = self.status();
        self.shared.emit_status(status.clone());
        Ok(status)
    }

    /// Pause immediately. When this returns, no further audio frames are emitted.
    pub fn pause(&self) -> Result<CaptureStatus> {
        let workers = self.workers();
        if workers.is_empty() || self.status().state != SessionState::Capturing {
            return Err(self.invalid("pause"));
        }
        self.gate_closed.store(true, Ordering::SeqCst);
        self.broadcast_and_wait(&workers, Control::Pause);
        self.shared.lock().state = SessionState::Paused;
        let status = self.status();
        self.shared.emit_status(status.clone());
        Ok(status)
    }

    pub fn resume(&self) -> Result<CaptureStatus> {
        let workers = self.workers();
        if workers.is_empty() || self.status().state != SessionState::Paused {
            return Err(self.invalid("resume"));
        }
        self.broadcast_and_wait(&workers, Control::Resume);
        self.gate_closed.store(false, Ordering::SeqCst);
        self.shared.lock().state = SessionState::Capturing;
        let status = self.status();
        self.shared.emit_status(status.clone());
        Ok(status)
    }

    /// Stop and release all OS streams. Idempotent.
    pub fn stop(&self) -> CaptureStatus {
        let mut workers = self.workers();
        self.gate_closed.store(true, Ordering::SeqCst);
        Self::shutdown_workers(&mut workers);
        self.shared.lock().state = SessionState::Idle;
        let status = self.status();
        self.shared.emit_status(status.clone());
        status
    }

    fn broadcast_and_wait(&self, workers: &[Worker], make: fn(SyncSender<()>) -> Control) {
        let mut acks = Vec::new();
        for w in workers {
            let (ack_tx, ack_rx) = mpsc::sync_channel(1);
            if w.tx.send(WorkerMsg::Control(make(ack_tx))).is_ok() {
                acks.push(ack_rx);
            }
        }
        for ack in acks {
            let _ = ack.recv_timeout(self.options.control_timeout);
        }
    }

    fn shutdown_workers(workers: &mut Vec<Worker>) {
        for w in workers.iter() {
            let _ = w.tx.send(WorkerMsg::Control(Control::Stop));
        }
        for mut w in workers.drain(..) {
            if let Some(j) = w.join.take() {
                let _ = j.join();
            }
        }
    }
}

impl Drop for CaptureSession {
    fn drop(&mut self) {
        let mut workers = self.workers();
        self.gate_closed.store(true, Ordering::SeqCst);
        Self::shutdown_workers(&mut workers);
    }
}

struct WorkerCtx {
    idx: usize,
    source: AudioSource,
    selection: SourceSelection,
    provider: Arc<dyn AudioCaptureProvider>,
    shared: Arc<Shared>,
    gate_closed: Arc<AtomicBool>,
    options: SessionOptions,
    tx: SyncSender<WorkerMsg>,
}

struct Pipeline {
    format: Option<NativeFormat>,
    resampler: Option<StreamingResampler>,
    framer: Framer,
    seq: u64,
    gap_pending: bool,
    level_sum_sq: f64,
    level_count: usize,
    level_peak: f32,
    last_level_emit: Instant,
    dropped_seen: u64,
}

enum Flow {
    Continue,
    Stop,
}

impl WorkerCtx {
    fn sink(&self, dropped: &Arc<AtomicU64>, pending: &PendingError) -> RawAudioSink {
        let tx = self.tx.clone();
        let send: SendFn = Arc::new(move |msg| match tx.try_send(WorkerMsg::Raw(msg)) {
            Ok(()) => SendOutcome::Sent,
            Err(TrySendError::Full(_)) => SendOutcome::Full,
            Err(TrySendError::Disconnected(_)) => SendOutcome::Closed,
        });
        RawAudioSink::new(
            send,
            Arc::clone(pending),
            Arc::clone(&self.gate_closed),
            Arc::clone(dropped),
            self.source,
        )
    }

    fn open(
        &self,
        dropped: &Arc<AtomicU64>,
        pending: &PendingError,
    ) -> Result<Box<dyn CaptureStream>> {
        let stream =
            self.provider
                .open(self.source, &self.selection, self.sink(dropped, pending))?;
        let (name, fmt) = (stream.device_name(), stream.native_format());
        self.shared.update_source(self.idx, |s| {
            s.device_name = Some(name);
            s.native_format = Some(fmt);
        });
        Ok(stream)
    }

    fn set_state(&self, state: SourceState) {
        let snap = self.shared.update_source(self.idx, |s| s.state = state);
        self.shared.emit_status(snap);
    }

    fn run(self, rx: Receiver<WorkerMsg>, open_tx: SyncSender<Result<()>>) {
        let dropped = Arc::new(AtomicU64::new(0));
        let pending: PendingError = Arc::new(Mutex::new(None));
        let mut stream = match self.open(&dropped, &pending) {
            Ok(s) => {
                // Publish the state before acknowledging, so `start()` returns accurate status.
                self.set_state(SourceState::Capturing);
                let _ = open_tx.send(Ok(()));
                Some(s)
            }
            Err(e) => {
                log::warn!("{} failed to open: {e}", self.source);
                self.shared.update_source(self.idx, |s| {
                    s.state = SourceState::Failed { error: e.clone() }
                });
                let _ = open_tx.send(Err(e));
                None
            }
        };

        let mut p = Pipeline {
            format: None,
            resampler: None,
            framer: Framer::default(),
            seq: 0,
            gap_pending: false,
            level_sum_sq: 0.0,
            level_count: 0,
            level_peak: 0.0,
            last_level_emit: Instant::now(),
            dropped_seen: 0,
        };
        let mut paused = false;
        let mut last_audio = Instant::now();

        loop {
            let parked = pending.lock().ok().and_then(|mut slot| slot.take());
            let msg = match parked {
                Some(err) => WorkerMsg::Raw(RawMsg::Error(err)),
                None => match rx.recv_timeout(Duration::from_millis(200)) {
                    Ok(m) => m,
                    Err(RecvTimeoutError::Timeout) => {
                        let stalled = self
                            .options
                            .stall_timeout
                            .is_some_and(|limit| last_audio.elapsed() > limit);
                        if stalled && !paused && stream.is_some() {
                            log::warn!("{} delivered no audio: reopening", self.source);
                            last_audio = Instant::now();
                            WorkerMsg::Raw(RawMsg::Error(CaptureError::DeviceUnavailable {
                                audio: self.source,
                                detail: "the device stopped sending audio".into(),
                            }))
                        } else {
                            continue;
                        }
                    }
                    Err(RecvTimeoutError::Disconnected) => break,
                },
            };
            match msg {
                WorkerMsg::Control(c) => {
                    last_audio = Instant::now();
                    match self.handle_control(c, &mut stream, &mut p, &mut paused) {
                        Ok(Flow::Stop) => break,
                        Ok(Flow::Continue) => {}
                        // e.g. the OS refused to restart the stream: treat as a stream failure
                        // so the normal reconnect path (and visible status) applies.
                        Err(e) => {
                            if let Ok(mut slot) = pending.lock() {
                                slot.get_or_insert(e);
                            }
                        }
                    }
                }
                WorkerMsg::Raw(RawMsg::Audio { samples, format }) => {
                    last_audio = Instant::now();
                    if paused || self.gate_closed.load(Ordering::SeqCst) || stream.is_none() {
                        continue; // discard audio captured around a pause
                    }
                    self.process(&mut p, &dropped, &samples, format);
                }
                WorkerMsg::Raw(RawMsg::Error(err)) => {
                    if stream.is_none() {
                        continue;
                    }
                    stream = None; // drop releases OS resources
                    log::warn!("{} stream error: {err}", self.source);
                    (self.shared.handler)(SessionEvent::Error(err.clone()));
                    if !err.is_recoverable() {
                        self.set_state(SourceState::Failed { error: err });
                        continue;
                    }
                    if let Ok(mut slot) = pending.lock() {
                        slot.take();
                    }
                    match self.reconnect(&rx, &dropped, &pending, &mut paused, err) {
                        Ok(Some(s)) => {
                            stream = Some(s);
                            last_audio = Instant::now();
                            p.gap_pending = true;
                            p.framer.clear();
                        }
                        Ok(None) => {}
                        Err(Flow::Stop) => break,
                        Err(Flow::Continue) => {}
                    }
                }
            }
        }
        drop(stream);
        self.shared.update_source(self.idx, |s| {
            if !matches!(s.state, SourceState::Failed { .. }) {
                s.state = SourceState::Stopped;
            }
        });
    }

    fn handle_control(
        &self,
        c: Control,
        stream: &mut Option<Box<dyn CaptureStream>>,
        p: &mut Pipeline,
        paused: &mut bool,
    ) -> std::result::Result<Flow, CaptureError> {
        match c {
            Control::Pause(ack) => {
                *paused = true;
                if let Some(s) = stream.as_mut() {
                    if let Err(e) = s.pause() {
                        log::debug!("OS-level pause unsupported for {}: {e}", self.source);
                    }
                    self.set_state(SourceState::Paused);
                }
                p.framer.clear();
                let _ = ack.send(());
                Ok(Flow::Continue)
            }
            Control::Resume(ack) => {
                *paused = false;
                p.gap_pending = true;
                let mut result = Ok(Flow::Continue);
                if let Some(s) = stream.as_mut() {
                    match s.resume() {
                        Ok(()) => self.set_state(SourceState::Capturing),
                        Err(e) => {
                            log::warn!("resume failed for {}: {e}", self.source);
                            result = Err(match e {
                                CaptureError::Unsupported { audio, reason } => {
                                    CaptureError::Backend {
                                        audio,
                                        detail: reason,
                                    }
                                }
                                other => other,
                            });
                        }
                    }
                }
                let _ = ack.send(());
                result
            }
            Control::Stop => Ok(Flow::Stop),
        }
    }

    /// Returns `Ok(Some(stream))` on success, `Ok(None)` if attempts were exhausted.
    fn reconnect(
        &self,
        rx: &Receiver<WorkerMsg>,
        dropped: &Arc<AtomicU64>,
        pending: &PendingError,
        paused: &mut bool,
        last_err: CaptureError,
    ) -> std::result::Result<Option<Box<dyn CaptureStream>>, Flow> {
        let mut last_err = last_err;
        for attempt in 1..=self.options.reconnect.max_attempts {
            self.set_state(SourceState::Reconnecting { attempt });
            // Wait for the backoff while staying responsive to user controls.
            let until = Instant::now() + self.options.reconnect.backoff;
            loop {
                let wait = until.saturating_duration_since(Instant::now());
                if wait.is_zero() {
                    break;
                }
                match rx.recv_timeout(wait) {
                    Ok(WorkerMsg::Control(Control::Stop)) => return Err(Flow::Stop),
                    Ok(WorkerMsg::Control(Control::Pause(ack))) => {
                        *paused = true;
                        let _ = ack.send(());
                    }
                    Ok(WorkerMsg::Control(Control::Resume(ack))) => {
                        *paused = false;
                        let _ = ack.send(());
                    }
                    Ok(WorkerMsg::Raw(_)) => {}
                    Err(RecvTimeoutError::Timeout) => break,
                    Err(RecvTimeoutError::Disconnected) => return Err(Flow::Stop),
                }
            }
            match self.open(dropped, pending) {
                Ok(mut s) => {
                    self.shared.update_source(self.idx, |st| st.reconnects += 1);
                    if *paused {
                        let _ = s.pause();
                        self.set_state(SourceState::Paused);
                    } else {
                        self.set_state(SourceState::Capturing);
                    }
                    return Ok(Some(s));
                }
                Err(e) => {
                    log::info!("{} reconnect attempt {attempt} failed: {e}", self.source);
                    let fatal = !e.is_recoverable();
                    last_err = e;
                    if fatal {
                        break;
                    }
                }
            }
        }
        self.set_state(SourceState::Failed {
            error: last_err.clone(),
        });
        (self.shared.handler)(SessionEvent::Error(last_err));
        Ok(None)
    }

    fn process(
        &self,
        p: &mut Pipeline,
        dropped: &Arc<AtomicU64>,
        samples: &[f32],
        format: NativeFormat,
    ) {
        if p.format != Some(format) {
            if p.format.is_some() {
                p.gap_pending = true;
            }
            p.format = Some(format);
            p.resampler = Some(StreamingResampler::new(
                format.sample_rate.max(1),
                TARGET_SAMPLE_RATE,
            ));
            p.framer.clear();
            self.shared
                .update_source(self.idx, |s| s.native_format = Some(format));
        }
        let dropped_now = dropped.load(Ordering::Relaxed);
        if dropped_now != p.dropped_seen {
            p.dropped_seen = dropped_now;
            p.gap_pending = true;
            self.shared
                .update_source(self.idx, |s| s.dropped_chunks = dropped_now);
        }

        let mono = downmix_to_mono(samples, format.channels);
        let resampled = match p.resampler.as_mut() {
            Some(r) => r.process(&mono),
            None => return,
        };
        for &s in &resampled {
            p.level_sum_sq += (s as f64) * (s as f64);
            p.level_peak = p.level_peak.max(s.abs());
        }
        p.level_count += resampled.len();
        if p.last_level_emit.elapsed() >= self.options.level_interval && p.level_count > 0 {
            let rms = (p.level_sum_sq / p.level_count as f64).sqrt() as f32;
            let (rms_dbfs, _) = levels(&[rms]);
            (self.shared.handler)(SessionEvent::Level(LevelReading {
                source: self.source,
                rms_dbfs,
                peak: p.level_peak.min(1.0),
            }));
            p.level_sum_sq = 0.0;
            p.level_count = 0;
            p.level_peak = 0.0;
            p.last_level_emit = Instant::now();
        }

        let pcm: Vec<i16> = resampled.iter().map(|&s| f32_to_i16(s)).collect();
        let frames = p.framer.push(&pcm);
        if frames.is_empty() {
            return;
        }
        let n = frames.len() as u64;
        for samples in frames {
            let frame = PcmFrame {
                source: self.source,
                seq: p.seq,
                offset_ms: p.seq * FRAME_DURATION_MS as u64,
                gap_before: std::mem::take(&mut p.gap_pending),
                samples,
            };
            p.seq += 1;
            (self.shared.handler)(SessionEvent::Frame(frame));
        }
        let seq = p.seq;
        self.shared.update_source(self.idx, |s| {
            s.frames_emitted += n;
            s.captured_ms = seq * FRAME_DURATION_MS as u64;
        });
    }
}
