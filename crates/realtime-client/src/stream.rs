//! Realtime audio uplink: sends 20 ms PCM frames to the backend gateway and relays server
//! events (transcripts, usage, errors). Survives network loss with bounded buffering.
//!
//! Guarantees (tested in `tests/stream.rs`):
//! * `send_frame` never blocks the caller (audio thread); overflow is counted, never hidden.
//! * Frames captured during a disconnect are buffered (bounded) and sent after reconnecting.
//! * A fresh stream token is fetched for every reconnect.
//! * Fatal server decisions (quota, unauthorized, meeting finished) stop retries.
//! * `stop()` waits for the server to flush final transcripts before returning.
//! * Frames written just before a connection drop may never reach the server, so the last
//!   `replay_frames` sent frames are re-sent after reconnecting; the gateway discards any
//!   frame whose sequence number it has already seen (no double transcription or billing).

use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use tokio::sync::{mpsc, oneshot};
use tokio::time::{sleep, timeout, Instant};
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::Message;

use crate::api::ApiError;

pub const PCM_SAMPLES: usize = 320;
pub const FRAME_BYTES: usize = 1 + 4 + PCM_SAMPLES * 2;

/// Close codes defined by the gateway (backend/app/realtime/gateway.py).
pub mod close {
    pub const BAD_FRAME: u16 = 4400;
    pub const UNAUTHORIZED: u16 = 4401;
    pub const QUOTA: u16 = 4402;
    pub const NOT_FOUND: u16 = 4404;
    pub const CONFLICT: u16 = 4409;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    System = 0,
    Microphone = 1,
}

/// Encode one frame: `[u8 source][u32 LE seq][320 × i16 LE]`.
pub fn encode_frame(source: Source, seq: u32, pcm: &[i16]) -> Option<Vec<u8>> {
    if pcm.len() != PCM_SAMPLES {
        return None;
    }
    let mut out = Vec::with_capacity(FRAME_BYTES);
    out.push(source as u8);
    out.extend_from_slice(&seq.to_le_bytes());
    for s in pcm {
        out.extend_from_slice(&s.to_le_bytes());
    }
    Some(out)
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum LinkStatus {
    Connecting,
    Connected,
    Reconnecting {
        attempt: u32,
        retry_in_ms: u64,
    },
    /// Ended normally (user stopped).
    Closed,
    /// Gave up; `reason` is user-facing.
    Failed {
        reason: String,
        code: Option<u16>,
    },
}

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Status(LinkStatus),
    /// A versioned envelope from the server, passed through as JSON.
    Server(serde_json::Value),
}

pub type EventSink = Arc<dyn Fn(StreamEvent) + Send + Sync>;
pub type TokenFuture = Pin<Box<dyn Future<Output = Result<String, ApiError>> + Send>>;
/// Fetches a fresh stream token (e.g. `POST /meetings/{id}/stream-token`).
pub type TokenProvider = Arc<dyn Fn() -> TokenFuture + Send + Sync>;

#[derive(Debug, Clone)]
pub struct StreamConfig {
    pub ws_url: String,
    /// Frames buffered while disconnected (1500 = 30 s of one source).
    pub queue_frames: usize,
    /// Recently sent frames re-sent after a reconnect (250 = 5 s of one source).
    pub replay_frames: usize,
    pub max_reconnect_attempts: u32,
    pub base_backoff: Duration,
    pub max_backoff: Duration,
    pub connect_timeout: Duration,
    pub stop_timeout: Duration,
}

impl StreamConfig {
    pub fn new(ws_url: impl Into<String>) -> Self {
        Self {
            ws_url: ws_url.into(),
            queue_frames: 1500,
            replay_frames: 250,
            max_reconnect_attempts: 20,
            base_backoff: Duration::from_millis(500),
            max_backoff: Duration::from_secs(10),
            connect_timeout: Duration::from_secs(10),
            stop_timeout: Duration::from_secs(10),
        }
    }
}

enum Control {
    Pause,
    Resume,
    Stop(oneshot::Sender<()>),
}

pub struct StreamHandle {
    audio: mpsc::Sender<Vec<u8>>,
    control: mpsc::UnboundedSender<Control>,
    dropped: Arc<AtomicU64>,
    finished: Arc<AtomicBool>,
}

impl StreamHandle {
    /// Queue a frame without blocking. Returns false if it was dropped (queue full / closed).
    pub fn send_frame(&self, source: Source, seq: u32, pcm: &[i16]) -> bool {
        let Some(bytes) = encode_frame(source, seq, pcm) else {
            return false;
        };
        if self.audio.try_send(bytes).is_ok() {
            true
        } else {
            self.dropped.fetch_add(1, Ordering::Relaxed);
            false
        }
    }

    pub fn dropped_frames(&self) -> u64 {
        self.dropped.load(Ordering::Relaxed)
    }

    pub fn is_finished(&self) -> bool {
        self.finished.load(Ordering::SeqCst)
    }

    pub fn pause(&self) {
        let _ = self.control.send(Control::Pause);
    }

    pub fn resume(&self) {
        let _ = self.control.send(Control::Resume);
    }

    /// Ask the server to finish (flushing final transcripts) and wait for the link to close.
    pub async fn stop(&self) {
        let (tx, rx) = oneshot::channel();
        if self.control.send(Control::Stop(tx)).is_ok() {
            let _ = rx.await;
        }
    }
}

/// Spawn the uplink on the current Tokio runtime. `initial_token` avoids a round trip for the
/// first connection; later connections use `tokens`.
pub fn spawn_stream(
    config: StreamConfig,
    initial_token: String,
    tokens: TokenProvider,
    events: EventSink,
) -> StreamHandle {
    let (audio_tx, audio_rx) = mpsc::channel(config.queue_frames.max(1));
    let (ctrl_tx, ctrl_rx) = mpsc::unbounded_channel();
    let dropped = Arc::new(AtomicU64::new(0));
    let finished = Arc::new(AtomicBool::new(false));
    let worker = Worker {
        config,
        tokens,
        events,
        audio: audio_rx,
        control: ctrl_rx,
        paused: false,
        stop_ack: None,
        replay: VecDeque::new(),
    };
    let fin = Arc::clone(&finished);
    tokio::spawn(async move {
        worker.run(initial_token).await;
        fin.store(true, Ordering::SeqCst);
    });
    StreamHandle {
        audio: audio_tx,
        control: ctrl_tx,
        dropped,
        finished,
    }
}

enum SessionEnd {
    /// User stopped and the server closed cleanly.
    Stopped,
    /// Connection lost or server asked to retry.
    Retry,
    Fatal {
        reason: String,
        code: Option<u16>,
    },
    /// Handle dropped: nobody is listening anymore.
    Abandoned,
}

struct Worker {
    config: StreamConfig,
    tokens: TokenProvider,
    events: EventSink,
    audio: mpsc::Receiver<Vec<u8>>,
    control: mpsc::UnboundedReceiver<Control>,
    paused: bool,
    stop_ack: Option<oneshot::Sender<()>>,
    replay: VecDeque<Vec<u8>>,
}

impl Worker {
    fn remember(&mut self, frame: &[u8]) {
        if self.config.replay_frames == 0 {
            return;
        }
        if self.replay.len() == self.config.replay_frames {
            self.replay.pop_front();
        }
        self.replay.push_back(frame.to_vec());
    }

    fn emit(&self, s: LinkStatus) {
        (self.events)(StreamEvent::Status(s));
    }

    fn backoff(&self, attempt: u32) -> Duration {
        let exp = self
            .config
            .base_backoff
            .saturating_mul(1u32 << attempt.min(6).saturating_sub(1));
        exp.min(self.config.max_backoff)
    }

    async fn run(mut self, initial_token: String) {
        let mut token = Some(initial_token);
        let mut attempt: u32 = 0;
        loop {
            self.emit(if attempt == 0 {
                LinkStatus::Connecting
            } else {
                LinkStatus::Reconnecting {
                    attempt,
                    retry_in_ms: 0,
                }
            });
            let tok = match token.take() {
                Some(t) => Ok(t),
                None => (self.tokens)().await,
            };
            let end = match tok {
                Ok(t) => self.session(&t, &mut attempt).await,
                Err(e) if e.is_transient() => SessionEnd::Retry,
                Err(e) => SessionEnd::Fatal {
                    reason: e.to_string(),
                    code: None,
                },
            };
            match end {
                SessionEnd::Stopped => {
                    self.emit(LinkStatus::Closed);
                    break;
                }
                SessionEnd::Abandoned => break,
                SessionEnd::Fatal { reason, code } => {
                    self.emit(LinkStatus::Failed { reason, code });
                    break;
                }
                SessionEnd::Retry => {
                    if self.stop_ack.is_some() {
                        // User already asked to stop and the link is gone: nothing to flush.
                        self.emit(LinkStatus::Closed);
                        break;
                    }
                    attempt += 1;
                    if attempt > self.config.max_reconnect_attempts {
                        self.emit(LinkStatus::Failed {
                            reason: "Could not reconnect to the MeetPilot server.".into(),
                            code: None,
                        });
                        break;
                    }
                    let wait = self.backoff(attempt);
                    self.emit(LinkStatus::Reconnecting {
                        attempt,
                        retry_in_ms: wait.as_millis() as u64,
                    });
                    // Stay responsive to stop/pause while waiting; audio keeps buffering.
                    let deadline = Instant::now() + wait;
                    loop {
                        tokio::select! {
                            _ = tokio::time::sleep_until(deadline) => break,
                            c = self.control.recv() => match c {
                                Some(Control::Pause) => self.paused = true,
                                Some(Control::Resume) => self.paused = false,
                                Some(Control::Stop(ack)) => { self.stop_ack = Some(ack); break; }
                                None => return,
                            }
                        }
                    }
                    if self.stop_ack.is_some() {
                        self.emit(LinkStatus::Closed);
                        break;
                    }
                }
            }
        }
        if let Some(ack) = self.stop_ack.take() {
            let _ = ack.send(());
        }
    }

    async fn session(&mut self, token: &str, attempt: &mut u32) -> SessionEnd {
        let connect = tokio_tungstenite::connect_async(self.config.ws_url.as_str());
        let mut ws = match timeout(self.config.connect_timeout, connect).await {
            Ok(Ok((ws, _))) => ws,
            Ok(Err(e)) => {
                log::info!("realtime connect failed: {e}");
                return SessionEnd::Retry;
            }
            Err(_) => return SessionEnd::Retry,
        };
        let auth = serde_json::json!({ "type": "auth", "token": token }).to_string();
        if ws.send(Message::text(auth)).await.is_err() {
            return SessionEnd::Retry;
        }

        // Wait for `capture.started` (or a close explaining why not).
        let first = timeout(self.config.connect_timeout, ws.next()).await;
        match first {
            Ok(Some(Ok(Message::Text(t)))) => {
                let v: serde_json::Value = serde_json::from_str(t.as_str()).unwrap_or_default();
                if v.get("type").and_then(|x| x.as_str()) != Some("capture.started") {
                    return SessionEnd::Retry;
                }
                *attempt = 0;
                self.emit(LinkStatus::Connected);
                (self.events)(StreamEvent::Server(v));
            }
            Ok(Some(Ok(Message::Close(frame)))) => {
                return classify_close(frame.map(|f| u16::from(f.code)), false)
            }
            _ => return SessionEnd::Retry,
        }

        // Re-send recent frames that may have been lost in flight (server dedups by seq).
        for frame in self.replay.clone() {
            if ws.send(Message::binary(frame)).await.is_err() {
                return SessionEnd::Retry;
            }
        }
        if self.paused {
            let msg = serde_json::json!({ "type": "capture.paused" }).to_string();
            if ws.send(Message::text(msg)).await.is_err() {
                return SessionEnd::Retry;
            }
        }
        if self.stop_ack.is_some() {
            let msg = serde_json::json!({ "type": "capture.stopped" }).to_string();
            let _ = ws.send(Message::text(msg)).await;
        }

        let mut ping = tokio::time::interval(Duration::from_secs(15));
        ping.tick().await;
        let mut stopping = self.stop_ack.is_some();
        let stop_deadline = sleep(Duration::from_secs(u64::MAX / 4));
        tokio::pin!(stop_deadline);
        if stopping {
            stop_deadline
                .as_mut()
                .reset(Instant::now() + self.config.stop_timeout);
        }

        loop {
            tokio::select! {
                biased;
                c = self.control.recv() => match c {
                    Some(Control::Pause) => {
                        self.paused = true;
                        let msg = serde_json::json!({ "type": "capture.paused" }).to_string();
                        if ws.send(Message::text(msg)).await.is_err() { return SessionEnd::Retry; }
                    }
                    Some(Control::Resume) => {
                        self.paused = false;
                        let msg = serde_json::json!({ "type": "capture.resumed" }).to_string();
                        if ws.send(Message::text(msg)).await.is_err() { return SessionEnd::Retry; }
                    }
                    Some(Control::Stop(ack)) => {
                        // Send what is already queued, then ask the server to finish.
                        while let Ok(frame) = self.audio.try_recv() {
                            self.remember(&frame);
                            if ws.send(Message::binary(frame)).await.is_err() { break; }
                        }
                        self.stop_ack = Some(ack);
                        stopping = true;
                        stop_deadline.as_mut().reset(Instant::now() + self.config.stop_timeout);
                        let msg = serde_json::json!({ "type": "capture.stopped" }).to_string();
                        if ws.send(Message::text(msg)).await.is_err() { return SessionEnd::Stopped; }
                    }
                    None => {
                        let _ = ws.close(None).await;
                        return SessionEnd::Abandoned;
                    }
                },
                frame = self.audio.recv(), if !stopping => match frame {
                    Some(bytes) => {
                        self.remember(&bytes);
                        if ws.send(Message::binary(bytes)).await.is_err() {
                            return SessionEnd::Retry;
                        }
                    }
                    None => {
                        let _ = ws.close(None).await;
                        return SessionEnd::Abandoned;
                    }
                },
                msg = ws.next() => match msg {
                    Some(Ok(Message::Text(t))) => {
                        if let Ok(v) = serde_json::from_str::<serde_json::Value>(t.as_str()) {
                            (self.events)(StreamEvent::Server(v));
                        }
                    }
                    Some(Ok(Message::Close(frame))) => {
                        return classify_close(frame.map(|f| u16::from(f.code)), stopping);
                    }
                    Some(Ok(_)) => {}
                    Some(Err(e)) => {
                        log::info!("realtime link error: {e}");
                        return if stopping { SessionEnd::Stopped } else { SessionEnd::Retry };
                    }
                    None => return if stopping { SessionEnd::Stopped } else { SessionEnd::Retry },
                },
                _ = ping.tick() => {
                    if ws.send(Message::Ping(Vec::new().into())).await.is_err() {
                        return SessionEnd::Retry;
                    }
                }
                _ = &mut stop_deadline, if stopping => {
                    let _ = ws.close(None).await;
                    return SessionEnd::Stopped;
                }
            }
        }
    }
}

fn classify_close(code: Option<u16>, stopping: bool) -> SessionEnd {
    match code {
        Some(close::QUOTA) => SessionEnd::Fatal {
            reason: "Your monthly transcription minutes are used up.".into(),
            code,
        },
        Some(close::UNAUTHORIZED) => SessionEnd::Fatal {
            reason: "The server rejected this session. Sign in again.".into(),
            code,
        },
        Some(close::NOT_FOUND) => SessionEnd::Fatal {
            reason: "This meeting is no longer active.".into(),
            code,
        },
        Some(close::BAD_FRAME) => SessionEnd::Fatal {
            reason: "The app sent audio the server did not accept. Please update MeetPilot.".into(),
            code,
        },
        // Another session may still be closing server-side: retry with backoff.
        Some(close::CONFLICT) => SessionEnd::Retry,
        _ if stopping => SessionEnd::Stopped,
        Some(c) if c == u16::from(CloseCode::Normal) => SessionEnd::Retry,
        _ => SessionEnd::Retry,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_layout_matches_gateway() {
        let pcm: Vec<i16> = (0..320).map(|i| i as i16 - 160).collect();
        let f = encode_frame(Source::Microphone, 0x0102_0304, &pcm).unwrap();
        assert_eq!(f.len(), 645);
        assert_eq!(f[0], 1);
        assert_eq!(&f[1..5], &[0x04, 0x03, 0x02, 0x01]);
        assert_eq!(i16::from_le_bytes([f[5], f[6]]), -160);
        assert!(encode_frame(Source::System, 0, &[0; 100]).is_none());
    }

    #[test]
    fn fatal_close_codes_stop_retries() {
        assert!(matches!(
            classify_close(Some(4402), false),
            SessionEnd::Fatal { .. }
        ));
        assert!(matches!(
            classify_close(Some(4401), false),
            SessionEnd::Fatal { .. }
        ));
        assert!(matches!(
            classify_close(Some(4404), false),
            SessionEnd::Fatal { .. }
        ));
        assert!(matches!(
            classify_close(Some(4409), false),
            SessionEnd::Retry
        ));
        assert!(matches!(
            classify_close(Some(1011), false),
            SessionEnd::Retry
        ));
        assert!(matches!(classify_close(None, true), SessionEnd::Stopped));
        assert!(matches!(
            classify_close(Some(1000), true),
            SessionEnd::Stopped
        ));
    }
}
