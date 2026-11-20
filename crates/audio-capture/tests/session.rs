//! Behavioural tests for CaptureSession using the synthetic provider (no real devices).

use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use meetpilot_audio::providers::synthetic::{SyntheticConfig, SyntheticProvider};
use meetpilot_audio::{
    AudioSource, CaptureConfig, CaptureError, CaptureSession, PcmFrame, ReconnectPolicy,
    SessionEvent, SessionOptions, SessionState, SourceSelection, SourceState, SAMPLES_PER_FRAME,
};

#[derive(Default)]
struct Recorder {
    frames: Vec<PcmFrame>,
    levels: usize,
    errors: Vec<CaptureError>,
    states: Vec<(AudioSource, SourceState)>,
}

fn session_with(
    provider: SyntheticProvider,
    options: SessionOptions,
    slow_consumer: Option<Duration>,
) -> (CaptureSession, Arc<Mutex<Recorder>>) {
    let rec = Arc::new(Mutex::new(Recorder::default()));
    let r = Arc::clone(&rec);
    let handler = Arc::new(move |ev: SessionEvent| {
        if let (Some(d), SessionEvent::Frame(_)) = (slow_consumer, &ev) {
            std::thread::sleep(d);
        }
        let mut r = r.lock().unwrap();
        match ev {
            SessionEvent::Frame(f) => r.frames.push(f),
            SessionEvent::Level(_) => r.levels += 1,
            SessionEvent::Error(e) => r.errors.push(e),
            SessionEvent::Status(s) => {
                for src in s.sources {
                    r.states.push((src.source, src.state));
                }
            }
        }
    });
    (
        CaptureSession::with_options(Arc::new(provider), handler, options),
        rec,
    )
}

fn fast_options() -> SessionOptions {
    SessionOptions {
        level_interval: Duration::from_millis(20),
        reconnect: ReconnectPolicy {
            max_attempts: 3,
            backoff: Duration::from_millis(30),
        },
        ..SessionOptions::default()
    }
}

fn system_only() -> CaptureConfig {
    CaptureConfig {
        system_audio: Some(SourceSelection::default()),
        microphone: None,
    }
}

fn both() -> CaptureConfig {
    CaptureConfig {
        system_audio: Some(SourceSelection::default()),
        microphone: Some(SourceSelection::default()),
    }
}

fn wait_until(timeout: Duration, mut f: impl FnMut() -> bool) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    f()
}

fn frame_count(rec: &Arc<Mutex<Recorder>>) -> usize {
    rec.lock().unwrap().frames.len()
}

#[test]
fn start_emits_normalized_frames_and_levels() {
    let (s, rec) = session_with(SyntheticProvider::default(), fast_options(), None);
    let status = s.start(&system_only()).unwrap();
    assert_eq!(status.state, SessionState::Capturing);
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) >= 20));
    s.stop();

    let r = rec.lock().unwrap();
    assert!(r.levels > 0, "level readings for the capture indicator");
    for (i, f) in r.frames.iter().enumerate() {
        assert_eq!(f.samples.len(), SAMPLES_PER_FRAME, "20 ms @ 16 kHz");
        assert_eq!(f.seq, i as u64, "contiguous sequence numbers");
        assert_eq!(f.offset_ms, i as u64 * 20);
        assert_eq!(f.source, AudioSource::System);
    }
    // A 0.25-amplitude tone must survive normalization with roughly that peak.
    let peak = r.frames[10..]
        .iter()
        .flat_map(|f| f.samples.iter())
        .map(|s| s.unsigned_abs())
        .max()
        .unwrap();
    assert!((7000..=9500).contains(&peak), "peak={peak}");
}

#[test]
fn status_reports_device_and_native_format() {
    let (s, _rec) = session_with(SyntheticProvider::default(), fast_options(), None);
    let st = s.start(&both()).unwrap();
    assert_eq!(st.sources.len(), 2);
    for src in &st.sources {
        assert_eq!(src.state, SourceState::Capturing);
        assert_eq!(src.device_name.as_deref(), Some("Synthetic tone"));
        assert_eq!(src.native_format.unwrap().sample_rate, 48_000);
    }
    s.stop();
}

#[test]
fn no_frames_are_emitted_while_paused() {
    let (s, rec) = session_with(SyntheticProvider::default(), fast_options(), None);
    s.start(&both()).unwrap();
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) >= 10));

    let st = s.pause().unwrap();
    assert_eq!(st.state, SessionState::Paused);
    assert!(st.sources.iter().all(|x| x.state == SourceState::Paused));
    let at_pause = frame_count(&rec);
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(frame_count(&rec), at_pause, "audio leaked while paused");

    s.resume().unwrap();
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) > at_pause + 5));
    s.stop();

    let r = rec.lock().unwrap();
    let first_after = &r.frames[at_pause..];
    for src in [AudioSource::System, AudioSource::Microphone] {
        let f = first_after.iter().find(|f| f.source == src).unwrap();
        assert!(f.gap_before, "resume must be marked as a gap for {src:?}");
    }
}

#[test]
fn no_frames_after_stop_and_restart_works() {
    let (s, rec) = session_with(SyntheticProvider::default(), fast_options(), None);
    s.start(&system_only()).unwrap();
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) >= 5));
    let st = s.stop();
    assert_eq!(st.state, SessionState::Idle);
    assert!(st.sources.iter().all(|x| x.state == SourceState::Stopped));
    let n = frame_count(&rec);
    std::thread::sleep(Duration::from_millis(150));
    assert_eq!(frame_count(&rec), n);

    // stop is idempotent; a new session can start
    s.stop();
    s.start(&system_only()).unwrap();
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) > n));
    s.stop();
}

#[test]
fn invalid_transitions_are_rejected() {
    let (s, _rec) = session_with(SyntheticProvider::default(), fast_options(), None);
    assert!(matches!(s.pause(), Err(CaptureError::InvalidState { .. })));
    assert!(matches!(s.resume(), Err(CaptureError::InvalidState { .. })));
    assert!(matches!(
        s.start(&CaptureConfig::default()),
        Err(CaptureError::InvalidConfig { .. })
    ));
    s.start(&system_only()).unwrap();
    assert!(matches!(
        s.start(&system_only()),
        Err(CaptureError::InvalidState { .. })
    ));
    assert!(matches!(s.resume(), Err(CaptureError::InvalidState { .. })));
    s.stop();
}

#[test]
fn permission_denied_on_every_source_fails_start_and_returns_to_idle() {
    let p = SyntheticProvider::default();
    p.faults.fail_opens_system.store(1, Ordering::SeqCst);
    *p.faults.open_error.lock().unwrap() = Some(CaptureError::PermissionDenied {
        audio: AudioSource::System,
        hint: "grant access".into(),
    });
    let (s, rec) = session_with(p, fast_options(), None);
    let err = s.start(&system_only()).unwrap_err();
    assert!(matches!(err, CaptureError::PermissionDenied { .. }));
    assert_eq!(s.status().state, SessionState::Idle);
    assert_eq!(frame_count(&rec), 0);
}

#[test]
fn partial_failure_keeps_working_source_and_reports_failed_one() {
    let p = SyntheticProvider::default();
    p.faults.fail_opens_mic.store(1, Ordering::SeqCst);
    let (s, rec) = session_with(p, fast_options(), None);
    let st = s.start(&both()).unwrap();
    let mic = st
        .sources
        .iter()
        .find(|x| x.source == AudioSource::Microphone)
        .unwrap();
    assert!(matches!(mic.state, SourceState::Failed { .. }));
    assert!(
        !rec.lock().unwrap().errors.is_empty(),
        "failure surfaced to UI"
    );
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) >= 5));
    assert!(rec
        .lock()
        .unwrap()
        .frames
        .iter()
        .all(|f| f.source == AudioSource::System));
    s.stop();
}

#[test]
fn device_loss_reconnects_automatically_with_visible_status_and_gap() {
    let p = SyntheticProvider::default();
    p.faults.disconnect_after_chunks.store(20, Ordering::SeqCst);
    let (s, rec) = session_with(p, fast_options(), None);
    s.start(&system_only()).unwrap();
    assert!(wait_until(Duration::from_secs(5), || {
        s.status().sources[0].reconnects == 1 && frame_count(&rec) > 30
    }));
    let st = s.stop();
    assert_eq!(st.sources[0].reconnects, 1);

    let r = rec.lock().unwrap();
    assert!(r
        .states
        .iter()
        .any(|(_, st)| matches!(st, SourceState::Reconnecting { attempt: 1 })));
    assert!(r
        .errors
        .iter()
        .any(|e| matches!(e, CaptureError::DeviceUnavailable { .. })));
    assert!(
        r.frames.iter().skip(1).any(|f| f.gap_before),
        "gap after reconnect"
    );
}

#[test]
fn reconnect_gives_up_after_policy_and_marks_failed() {
    let p = SyntheticProvider::default();
    p.faults.disconnect_after_chunks.store(5, Ordering::SeqCst);
    let faults = Arc::clone(&p.faults);
    let (s, rec) = session_with(p, fast_options(), None);
    s.start(&system_only()).unwrap();
    // Every reconnect attempt fails from now on.
    faults.fail_opens_system.store(100, Ordering::SeqCst);
    assert!(wait_until(Duration::from_secs(5), || matches!(
        s.status().sources[0].state,
        SourceState::Failed { .. }
    )));
    let r = rec.lock().unwrap();
    assert!(r
        .states
        .iter()
        .any(|(_, st)| matches!(st, SourceState::Reconnecting { attempt: 3 })));
    drop(r);
    s.stop();
}

#[test]
fn slow_consumer_causes_counted_drops_not_blocking() {
    let p = SyntheticProvider::new(SyntheticConfig {
        realtime: false,
        ..SyntheticConfig::default()
    });
    let opts = SessionOptions {
        raw_queue_chunks: 2,
        ..fast_options()
    };
    let (s, rec) = session_with(p, opts, Some(Duration::from_millis(2)));
    s.start(&system_only()).unwrap();
    assert!(wait_until(Duration::from_secs(5), || s.status().sources[0]
        .dropped_chunks
        > 0));
    s.stop();
    assert!(rec.lock().unwrap().frames.iter().any(|f| f.gap_before));
}

#[test]
fn pause_during_reconnect_is_honoured() {
    let p = SyntheticProvider::default();
    p.faults.disconnect_after_chunks.store(5, Ordering::SeqCst);
    let opts = SessionOptions {
        reconnect: ReconnectPolicy {
            max_attempts: 3,
            backoff: Duration::from_millis(200),
        },
        ..fast_options()
    };
    let (s, rec) = session_with(p, opts, None);
    s.start(&system_only()).unwrap();
    assert!(wait_until(Duration::from_secs(3), || matches!(
        s.status().sources[0].state,
        SourceState::Reconnecting { .. }
    )));
    s.pause().unwrap();
    let n = frame_count(&rec);
    // Reconnect completes while paused; still no frames.
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(s.status().sources[0].state, SourceState::Paused);
    assert_eq!(frame_count(&rec), n);
    s.resume().unwrap();
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) > n));
    s.stop();
}

#[test]
fn errors_and_status_serialize_for_the_ui() {
    let e = CaptureError::PermissionDenied {
        audio: AudioSource::Microphone,
        hint: "x".into(),
    };
    let v = serde_json::to_value(&e).unwrap();
    assert_eq!(v["kind"], "permission_denied");
    assert_eq!(v["source"], "microphone");
    let back: CaptureError = serde_json::from_value(v).unwrap();
    assert_eq!(back, e);

    let (s, _rec) = session_with(SyntheticProvider::default(), fast_options(), None);
    let st = s.start(&system_only()).unwrap();
    let v = serde_json::to_value(&st).unwrap();
    assert_eq!(v["state"], "capturing");
    assert_eq!(v["sources"][0]["state"], "capturing");
    assert_eq!(v["sources"][0]["source"], "system");
    s.stop();
}

#[test]
fn failed_os_resume_triggers_reconnect_instead_of_silent_stall() {
    let p = SyntheticProvider::default();
    let faults = Arc::clone(&p.faults);
    let (s, rec) = session_with(p, fast_options(), None);
    s.start(&system_only()).unwrap();
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) >= 5));
    s.pause().unwrap();
    faults.fail_next_resume.store(true, Ordering::SeqCst);
    s.resume().unwrap();
    let n = frame_count(&rec);
    assert!(wait_until(Duration::from_secs(3), || {
        s.status().sources[0].reconnects == 1 && frame_count(&rec) > n + 5
    }));
    assert_eq!(s.status().sources[0].state, SourceState::Capturing);
    s.stop();
}

#[test]
fn a_device_that_silently_stops_sending_is_reopened() {
    let p = SyntheticProvider::default();
    p.faults.disconnect_after_chunks.store(5, Ordering::SeqCst);
    p.faults.silent_disconnect.store(true, Ordering::SeqCst);
    let opts = SessionOptions {
        stall_timeout: Some(Duration::from_millis(300)),
        ..fast_options()
    };
    let (s, rec) = session_with(p, opts, None);
    s.start(&system_only()).unwrap();
    assert!(
        wait_until(Duration::from_secs(5), || s.status().sources[0].reconnects
            >= 1),
        "watchdog should reopen the silent stream: {:?}",
        s.status()
    );
    let before = frame_count(&rec);
    assert!(wait_until(Duration::from_secs(3), || frame_count(&rec) > before + 5));
    assert!(matches!(
        s.status().sources[0].state,
        SourceState::Capturing
    ));
    s.stop();
}

#[test]
fn no_watchdog_reopen_while_paused() {
    let p = SyntheticProvider::default();
    let opts = SessionOptions {
        stall_timeout: Some(Duration::from_millis(200)),
        ..fast_options()
    };
    let (s, _rec) = session_with(p, opts, None);
    s.start(&system_only()).unwrap();
    s.pause().unwrap();
    std::thread::sleep(Duration::from_millis(800));
    assert_eq!(s.status().sources[0].reconnects, 0);
    s.stop();
}
