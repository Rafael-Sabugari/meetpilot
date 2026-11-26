//! On-device transcription engine.
//!
//! Frames arrive from the capture threads (`push_frame`, cheap and non-blocking for long);
//! a per-source [`Segmenter`] turns them into utterances; a single worker thread runs the
//! recognizer. Finals have priority and are never silently dropped; for partials only the
//! newest per source is kept, so a slow computer degrades to fewer live updates instead of
//! a growing delay.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::segmenter::{Segmenter, SegmenterConfig, Utterance};
use crate::speakers::{Diarizer, SpeakerEmbedderFactory};
use crate::text;

/// Speech recognizer used by the worker thread (Whisper in production, fakes in tests).
pub trait Recognizer: Send {
    /// Transcribe 16 kHz mono f32 audio. `language` is a Whisper code ("pt", "en") or "auto".
    fn transcribe(&mut self, audio: &[f32], language: &str) -> Result<String, String>;
}

/// Builds the recognizer on the worker thread (loading a model can take seconds).
pub type RecognizerFactory = Box<dyn FnOnce() -> Result<Box<dyn Recognizer>, String> + Send>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    System,
    Microphone,
}

impl Source {
    fn as_str(self) -> &'static str {
        match self {
            Source::System => "system",
            Source::Microphone => "microphone",
        }
    }
}

/// Same shape as the server's `TranscriptSegment`, so the UI handles both engines alike.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LocalSegment {
    pub segment_id: String,
    pub source: Source,
    pub text: String,
    pub language: Option<String>,
    pub start_ms: u64,
    pub end_ms: u64,
    pub is_final: bool,
    pub speaker: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum LocalEvent {
    /// Model loaded; transcription is live.
    Ready,
    Segment(LocalSegment),
    /// Finals waiting for the model (the computer is not keeping up when this grows).
    Backlog {
        pending_finals: usize,
    },
    /// A final was dropped because the backlog limit was reached (never silent).
    Dropped {
        utterance_ms: u64,
    },
    Error(String),
    Stopped,
}

pub type EventSink = Arc<dyn Fn(LocalEvent) + Send + Sync>;

#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// Whisper language code: "pt", "en" or "auto".
    pub whisper_language: String,
    /// Label reported on segments (e.g. "pt-BR").
    pub language_label: String,
    pub segmenter: SegmenterConfig,
    pub max_pending_finals: usize,
}

impl EngineConfig {
    /// From the app's meeting language ("pt-BR" | "en" | "multi").
    pub fn for_language(language: &str) -> Self {
        let whisper = match language {
            "pt-BR" | "pt" => "pt",
            "en" => "en",
            _ => "auto",
        };
        Self {
            whisper_language: whisper.into(),
            language_label: language.into(),
            segmenter: SegmenterConfig::default(),
            max_pending_finals: 40,
        }
    }
}

struct Job {
    source: Source,
    utterance: Utterance,
}

#[derive(Default)]
struct Queue {
    finals: VecDeque<Job>,
    partials: HashMap<Source, Job>,
    stopping: bool,
    aborted: bool,
}

struct Shared {
    queue: Mutex<Queue>,
    cv: Condvar,
    sink: EventSink,
    cfg: EngineConfig,
}

impl Shared {
    fn lock(&self) -> std::sync::MutexGuard<'_, Queue> {
        self.queue.lock().unwrap_or_else(|p| p.into_inner())
    }

    fn enqueue(&self, source: Source, utterance: Utterance) {
        let mut q = self.lock();
        if q.stopping {
            return;
        }
        if utterance.is_final {
            // A pending partial of this source is obsolete now.
            q.partials.remove(&source);
            q.finals.push_back(Job { source, utterance });
            if q.finals.len() > self.cfg.max_pending_finals {
                if let Some(old) = q.finals.pop_front() {
                    (self.sink)(LocalEvent::Dropped {
                        utterance_ms: old.utterance.duration_ms(),
                    });
                }
            }
            let pending = q.finals.len();
            drop(q);
            if pending >= 3 {
                (self.sink)(LocalEvent::Backlog {
                    pending_finals: pending,
                });
            }
        } else {
            q.partials.insert(source, Job { source, utterance });
            drop(q);
        }
        self.cv.notify_one();
    }
}

pub struct LocalTranscriber {
    shared: Arc<Shared>,
    segmenters: Mutex<HashMap<Source, Segmenter>>,
    paused: AtomicBool,
    worker: Mutex<Option<JoinHandle<()>>>,
    done: Mutex<Option<mpsc::Receiver<()>>>,
}

impl LocalTranscriber {
    pub fn start(factory: RecognizerFactory, cfg: EngineConfig, sink: EventSink) -> Self {
        Self::start_with_speakers(factory, None, cfg, sink)
    }

    /// Like `start`, also labelling who is speaking among the others ("Pessoa 1"…) when
    /// a speaker model is given. Speaker trouble never stops transcription.
    pub fn start_with_speakers(
        factory: RecognizerFactory,
        speakers: Option<SpeakerEmbedderFactory>,
        cfg: EngineConfig,
        sink: EventSink,
    ) -> Self {
        let shared = Arc::new(Shared {
            queue: Mutex::new(Queue::default()),
            cv: Condvar::new(),
            sink,
            cfg,
        });
        let (done_tx, done_rx) = mpsc::sync_channel(1);
        let s = Arc::clone(&shared);
        let worker = std::thread::Builder::new()
            .name("mp-local-stt".into())
            .spawn(move || {
                run_worker(&s, factory, speakers);
                (s.sink)(LocalEvent::Stopped);
                let _ = done_tx.send(());
            })
            .expect("spawn local STT worker");
        Self {
            shared,
            segmenters: Mutex::new(HashMap::new()),
            paused: AtomicBool::new(false),
            worker: Mutex::new(Some(worker)),
            done: Mutex::new(Some(done_rx)),
        }
    }

    /// Feed one 20 ms frame (16 kHz mono PCM16). Ignored while paused.
    pub fn push_frame(&self, source: Source, seq: u64, frame: &[i16]) {
        if self.paused.load(Ordering::Acquire) {
            return;
        }
        let decision = {
            let mut segs = self.segmenters.lock().unwrap_or_else(|p| p.into_inner());
            segs.entry(source)
                .or_insert_with(|| Segmenter::new(self.shared.cfg.segmenter.clone()))
                .push(seq, frame)
        };
        if let Some(u) = decision {
            self.shared.enqueue(source, u);
        }
    }

    fn flush_all(&self) {
        let pending: Vec<(Source, Utterance)> = {
            let mut segs = self.segmenters.lock().unwrap_or_else(|p| p.into_inner());
            segs.iter_mut()
                .filter_map(|(src, s)| s.flush().map(|u| (*src, u)))
                .collect()
        };
        for (src, u) in pending {
            self.shared.enqueue(src, u);
        }
    }

    /// Pause: what was said so far is finalized; new audio is ignored.
    pub fn pause(&self) {
        self.paused.store(true, Ordering::Release);
        self.flush_all();
    }

    pub fn resume(&self) {
        self.paused.store(false, Ordering::Release);
    }

    /// Finalize pending speech and wait (up to `timeout`) for it to be transcribed.
    /// Returns false if the timeout hit (remaining work is abandoned, never left running).
    pub fn stop(&self, timeout: Duration) -> bool {
        self.paused.store(true, Ordering::Release);
        self.flush_all();
        {
            let mut q = self.shared.lock();
            q.stopping = true;
            q.partials.clear();
        }
        self.shared.cv.notify_all();
        let done = self.done.lock().unwrap_or_else(|p| p.into_inner()).take();
        let finished = done.is_some_and(|rx| rx.recv_timeout(timeout).is_ok());
        if !finished {
            self.shared.lock().aborted = true;
            self.shared.cv.notify_all();
        }
        let worker = self.worker.lock().unwrap_or_else(|p| p.into_inner()).take();
        if let Some(w) = worker {
            if finished {
                let _ = w.join();
            }
            // Otherwise the worker exits after its current decode; we do not block on it.
        }
        finished
    }
}

impl Drop for LocalTranscriber {
    fn drop(&mut self) {
        let mut q = self.shared.lock();
        q.stopping = true;
        q.aborted = true;
        drop(q);
        self.shared.cv.notify_all();
    }
}

fn run_worker(
    shared: &Shared,
    factory: RecognizerFactory,
    speakers: Option<SpeakerEmbedderFactory>,
) {
    let mut recognizer = match factory() {
        Ok(r) => r,
        Err(e) => {
            (shared.sink)(LocalEvent::Error(e));
            return;
        }
    };
    let mut diarizer = speakers.and_then(|f| match f() {
        Ok(e) => Some(Diarizer::new(e)),
        Err(e) => {
            (shared.sink)(LocalEvent::Error(format!("Speaker labels are off: {e}")));
            None
        }
    });
    (shared.sink)(LocalEvent::Ready);
    let language = shared.cfg.whisper_language.clone();
    let label = shared.cfg.language_label.clone();
    let mut rotate = 0usize;

    loop {
        let job = {
            let mut q = shared.lock();
            loop {
                if q.aborted {
                    return;
                }
                if let Some(j) = q.finals.pop_front() {
                    break j;
                }
                if q.stopping {
                    return;
                }
                if !q.partials.is_empty() {
                    // Alternate sources so one speaker cannot starve the other's partials.
                    let mut keys: Vec<Source> = q.partials.keys().copied().collect();
                    keys.sort_by_key(|s| s.as_str());
                    let key = keys[rotate % keys.len()];
                    rotate = rotate.wrapping_add(1);
                    break q.partials.remove(&key).expect("key present");
                }
                q = shared.cv.wait(q).unwrap_or_else(|p| p.into_inner());
            }
        };

        let u = &job.utterance;
        // Whisper works on >= 1 s windows; pad short utterances with silence.
        let mut audio = u.audio.clone();
        if audio.len() < 16_000 {
            audio.resize(16_000, 0.0);
        }
        normalize(&mut audio);
        let started = Instant::now();
        match recognizer.transcribe(&audio, &language) {
            Ok(raw) => {
                log::debug!(
                    "local stt: {} ms audio in {} ms",
                    u.duration_ms(),
                    started.elapsed().as_millis()
                );
                if let Some(text) = text::clean(&raw) {
                    // Only the others' finished phrases (the microphone is the User).
                    let speaker = match (&mut diarizer, job.source, u.is_final) {
                        (Some(d), Source::System, true) => {
                            d.speaker(&u.audio, u.start_ms(), u.end_ms())
                        }
                        _ => None,
                    };
                    (shared.sink)(LocalEvent::Segment(LocalSegment {
                        segment_id: format!("{}-{}", job.source.as_str(), u.id),
                        source: job.source,
                        text,
                        language: Some(label.clone()),
                        start_ms: u.start_ms(),
                        end_ms: u.end_ms(),
                        is_final: u.is_final,
                        speaker,
                    }));
                }
            }
            Err(e) => (shared.sink)(LocalEvent::Error(e)),
        }
    }
}

/// Bring quiet speech (a meeting played at low volume) up to a level Whisper handles well.
/// Peak-normalised to about -6 dBFS, with at most +30 dB of gain; loud audio is untouched.
pub fn normalize(audio: &mut [f32]) {
    let peak = audio.iter().fold(0f32, |m, s| m.max(s.abs()));
    if peak <= 1e-6 || peak >= 0.5 {
        return;
    }
    let gain = (0.5 / peak).min(31.6);
    for s in audio.iter_mut() {
        *s *= gain;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quiet_audio_is_brought_up_and_loud_audio_left_alone() {
        let mut quiet = vec![0.01, -0.02, 0.005];
        normalize(&mut quiet);
        assert!((quiet[1].abs() - 0.5).abs() < 1e-4);
        let mut whisper_quiet = vec![0.000_1, -0.000_2];
        normalize(&mut whisper_quiet);
        assert!(whisper_quiet[1].abs() < 0.01, "gain is capped at +30 dB");
        let mut loud = vec![0.8, -0.6];
        normalize(&mut loud);
        assert_eq!(loud, vec![0.8, -0.6]);
    }
    use std::sync::atomic::AtomicUsize;

    /// Reports the audio length it was given; optionally slow.
    struct Fake {
        delay: Duration,
        calls: Arc<AtomicUsize>,
    }

    impl Recognizer for Fake {
        fn transcribe(&mut self, audio: &[f32], language: &str) -> Result<String, String> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            std::thread::sleep(self.delay);
            Ok(format!("{language} {} ms", audio.len() / 16))
        }
    }

    fn collect() -> (EventSink, Arc<Mutex<Vec<LocalEvent>>>) {
        let ev = Arc::new(Mutex::new(Vec::new()));
        let e2 = Arc::clone(&ev);
        (Arc::new(move |e| e2.lock().unwrap().push(e)), ev)
    }

    fn speech(seq: u64) -> Vec<i16> {
        (0..320)
            .map(|i| (((seq as usize * 320 + i) as f32 * 0.12).sin() * 6000.0) as i16)
            .collect()
    }

    fn feed(t: &LocalTranscriber, src: Source, range: std::ops::Range<u64>, voiced: bool) {
        for seq in range {
            let f = if voiced { speech(seq) } else { vec![0; 320] };
            t.push_frame(src, seq, &f);
        }
    }

    fn finals(ev: &Arc<Mutex<Vec<LocalEvent>>>) -> Vec<LocalSegment> {
        ev.lock()
            .unwrap()
            .iter()
            .filter_map(|e| match e {
                LocalEvent::Segment(s) if s.is_final => Some(s.clone()),
                _ => None,
            })
            .collect()
    }

    fn fake(delay_ms: u64) -> (RecognizerFactory, Arc<AtomicUsize>) {
        let calls = Arc::new(AtomicUsize::new(0));
        let c = Arc::clone(&calls);
        (
            Box::new(move || {
                Ok(Box::new(Fake {
                    delay: Duration::from_millis(delay_ms),
                    calls: c,
                }) as Box<dyn Recognizer>)
            }),
            calls,
        )
    }

    #[test]
    fn transcribes_each_source_with_meeting_timestamps() {
        let (sink, ev) = collect();
        let (factory, _) = fake(0);
        let t = LocalTranscriber::start(factory, EngineConfig::for_language("pt-BR"), sink);
        feed(&t, Source::System, 0..100, true);
        feed(&t, Source::System, 100..150, false);
        feed(&t, Source::Microphone, 500..600, true);
        assert!(t.stop(Duration::from_secs(5)));

        let f = finals(&ev);
        assert_eq!(f.len(), 2);
        let sys = f.iter().find(|s| s.source == Source::System).unwrap();
        assert_eq!(sys.start_ms, 0);
        assert!(
            sys.text.starts_with("pt "),
            "language passed to whisper: {}",
            sys.text
        );
        assert_eq!(sys.language.as_deref(), Some("pt-BR"));
        let mic = f.iter().find(|s| s.source == Source::Microphone).unwrap();
        assert_eq!(
            mic.start_ms,
            500 * 20,
            "flushed on stop, meeting-relative time"
        );
        let events = ev.lock().unwrap();
        assert_eq!(events.first(), Some(&LocalEvent::Ready));
        assert_eq!(events.last(), Some(&LocalEvent::Stopped));
    }

    #[test]
    fn silence_costs_no_model_calls() {
        let (sink, _ev) = collect();
        let (factory, calls) = fake(0);
        let t = LocalTranscriber::start(factory, EngineConfig::for_language("en"), sink);
        feed(&t, Source::System, 0..3_000, false); // 60 s of silence
        assert!(t.stop(Duration::from_secs(5)));
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn slow_model_coalesces_partials_but_keeps_every_final() {
        let (sink, ev) = collect();
        let (factory, calls) = fake(300); // slower than real time for partials
        let t = LocalTranscriber::start(factory, EngineConfig::for_language("en"), sink);
        let mut seq = 0;
        for _ in 0..4 {
            feed(&t, Source::System, seq..seq + 200, true); // 4 s speech
            feed(&t, Source::System, seq + 200..seq + 250, false); // pause
            seq += 250;
        }
        assert!(t.stop(Duration::from_secs(10)));
        assert_eq!(finals(&ev).len(), 4, "no final lost");
        let partials = ev
            .lock()
            .unwrap()
            .iter()
            .filter(|e| matches!(e, LocalEvent::Segment(s) if !s.is_final))
            .count();
        // Without coalescing there would be ~2 partials per utterance (8); the slow model
        // must not be asked for all of them.
        assert!(calls.load(Ordering::SeqCst) <= 4 + partials);
    }

    #[test]
    fn paused_audio_is_ignored_and_pause_finalizes() {
        let (sink, ev) = collect();
        let (factory, calls) = fake(0);
        let t = LocalTranscriber::start(factory, EngineConfig::for_language("en"), sink);
        feed(&t, Source::System, 0..60, true);
        t.pause();
        std::thread::sleep(Duration::from_millis(100));
        let after_pause = calls.load(Ordering::SeqCst);
        feed(&t, Source::System, 60..600, true); // ignored
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(calls.load(Ordering::SeqCst), after_pause);
        assert_eq!(finals(&ev).len(), 1, "speech before pause finalized");
        t.resume();
        feed(&t, Source::System, 600..700, true);
        assert!(t.stop(Duration::from_secs(5)));
        assert_eq!(finals(&ev).len(), 2);
    }

    #[test]
    fn model_load_failure_is_reported() {
        let (sink, ev) = collect();
        let factory: RecognizerFactory = Box::new(|| Err("model file is corrupted".into()));
        let t = LocalTranscriber::start(factory, EngineConfig::for_language("en"), sink);
        assert!(t.stop(Duration::from_secs(2)));
        let events = ev.lock().unwrap();
        assert_eq!(
            events[0],
            LocalEvent::Error("model file is corrupted".into())
        );
    }

    #[test]
    fn backlog_limit_drops_oldest_final_visibly() {
        let (sink, ev) = collect();
        let (factory, _) = fake(200);
        let cfg = EngineConfig {
            max_pending_finals: 2,
            ..EngineConfig::for_language("en")
        };
        let t = LocalTranscriber::start(factory, cfg, sink);
        let mut seq = 0;
        for _ in 0..8 {
            feed(&t, Source::System, seq..seq + 30, true);
            feed(&t, Source::System, seq + 30..seq + 70, false);
            seq += 70;
        }
        t.stop(Duration::from_secs(10));
        let dropped = ev
            .lock()
            .unwrap()
            .iter()
            .filter(|e| matches!(e, LocalEvent::Dropped { .. }))
            .count();
        assert!(dropped > 0, "drops are reported");
        assert_eq!(finals(&ev).len() + dropped, 8);
    }

    #[test]
    fn language_mapping() {
        assert_eq!(EngineConfig::for_language("pt-BR").whisper_language, "pt");
        assert_eq!(EngineConfig::for_language("en").whisper_language, "en");
        assert_eq!(EngineConfig::for_language("multi").whisper_language, "auto");
    }
}
