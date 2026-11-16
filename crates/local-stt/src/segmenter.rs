//! Decides *when* to run Whisper on a stream of 20 ms, 16 kHz PCM frames.
//!
//! Whisper is not a streaming model, so we cut the audio into utterances with a light
//! energy-based voice-activity detector:
//! * silence is never sent to the model (saves CPU and avoids Whisper's well-known
//!   hallucinations on silence, e.g. "Obrigado por assistir");
//! * while someone speaks, a *partial* is requested every `partial_every_ms`;
//! * a *final* is produced after `end_silence_ms` of silence, or when an utterance reaches
//!   `max_utterance_ms` (long monologues are split).

use std::collections::VecDeque;

pub const FRAME_MS: u32 = 20;
pub const SAMPLE_RATE: usize = 16_000;

#[derive(Debug, Clone)]
pub struct SegmenterConfig {
    /// Frames louder than this (and than the noise floor + margin) count as speech.
    pub threshold_dbfs: f32,
    pub noise_margin_db: f32,
    pub end_silence_ms: u32,
    pub max_utterance_ms: u32,
    pub partial_every_ms: u32,
    /// Utterances with less speech than this are discarded (clicks, coughs).
    pub min_speech_ms: u32,
    /// Audio kept from just before speech starts, so first syllables are not clipped.
    pub pre_roll_ms: u32,
}

impl Default for SegmenterConfig {
    fn default() -> Self {
        Self {
            threshold_dbfs: -45.0,
            noise_margin_db: 10.0,
            end_silence_ms: 700,
            max_utterance_ms: 15_000,
            partial_every_ms: 1_500,
            min_speech_ms: 300,
            pre_roll_ms: 240,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Utterance {
    /// Stable id shared by the partials and the final of one utterance.
    pub id: u64,
    pub start_seq: u64,
    pub end_seq: u64,
    pub is_final: bool,
    /// 16 kHz mono f32 in [-1, 1].
    pub audio: Vec<f32>,
}

impl Utterance {
    pub fn start_ms(&self) -> u64 {
        self.start_seq * FRAME_MS as u64
    }
    pub fn end_ms(&self) -> u64 {
        (self.end_seq + 1) * FRAME_MS as u64
    }
    pub fn duration_ms(&self) -> u64 {
        (self.audio.len() as u64 * 1000) / SAMPLE_RATE as u64
    }
}

#[derive(Debug)]
struct Active {
    id: u64,
    start_seq: u64,
    last_seq: u64,
    audio: Vec<f32>,
    speech_ms: u32,
    silence_ms: u32,
    since_partial_ms: u32,
}

#[derive(Debug)]
pub struct Segmenter {
    cfg: SegmenterConfig,
    noise_floor_db: f32,
    pre_roll: VecDeque<(u64, Vec<f32>)>,
    active: Option<Active>,
    next_id: u64,
}

pub fn frame_dbfs(frame: &[i16]) -> f32 {
    if frame.is_empty() {
        return -100.0;
    }
    let sum: f64 = frame.iter().map(|&s| (s as f64 / 32768.0).powi(2)).sum();
    let rms = (sum / frame.len() as f64).sqrt();
    if rms <= 1e-5 {
        -100.0
    } else {
        (20.0 * rms.log10()) as f32
    }
}

fn to_f32(frame: &[i16]) -> Vec<f32> {
    frame.iter().map(|&s| s as f32 / 32768.0).collect()
}

impl Segmenter {
    pub fn new(cfg: SegmenterConfig) -> Self {
        Self {
            cfg,
            noise_floor_db: -60.0,
            pre_roll: VecDeque::new(),
            active: None,
            next_id: 0,
        }
    }

    pub fn is_in_utterance(&self) -> bool {
        self.active.is_some()
    }

    fn threshold(&self) -> f32 {
        self.cfg
            .threshold_dbfs
            .max(self.noise_floor_db + self.cfg.noise_margin_db)
    }

    /// Feed one 20 ms frame. Returns a partial or final utterance when one is due.
    pub fn push(&mut self, seq: u64, frame: &[i16]) -> Option<Utterance> {
        let db = frame_dbfs(frame);
        let speech = db > self.threshold();

        let Some(active) = self.active.as_mut() else {
            if speech {
                let mut audio = Vec::new();
                let start_seq = self.pre_roll.front().map_or(seq, |(s, _)| *s);
                for (_, f) in self.pre_roll.drain(..) {
                    audio.extend_from_slice(&f);
                }
                audio.extend(to_f32(frame));
                self.active = Some(Active {
                    id: self.next_id,
                    start_seq,
                    last_seq: seq,
                    audio,
                    speech_ms: FRAME_MS,
                    silence_ms: 0,
                    since_partial_ms: FRAME_MS,
                });
                self.next_id += 1;
            } else {
                // Track background noise slowly so a noisy room does not look like speech.
                self.noise_floor_db = (0.95 * self.noise_floor_db + 0.05 * db).clamp(-90.0, -25.0);
                self.pre_roll.push_back((seq, to_f32(frame)));
                let keep = (self.cfg.pre_roll_ms / FRAME_MS) as usize;
                while self.pre_roll.len() > keep {
                    self.pre_roll.pop_front();
                }
            }
            return None;
        };

        active.audio.extend(to_f32(frame));
        active.last_seq = seq;
        active.since_partial_ms += FRAME_MS;
        if speech {
            active.speech_ms += FRAME_MS;
            active.silence_ms = 0;
        } else {
            active.silence_ms += FRAME_MS;
        }
        let duration_ms = (active.audio.len() * 1000 / SAMPLE_RATE) as u32;

        if active.silence_ms >= self.cfg.end_silence_ms || duration_ms >= self.cfg.max_utterance_ms
        {
            return self.finish();
        }
        if active.since_partial_ms >= self.cfg.partial_every_ms
            && active.speech_ms >= self.cfg.min_speech_ms
        {
            active.since_partial_ms = 0;
            return Some(Utterance {
                id: active.id,
                start_seq: active.start_seq,
                end_seq: active.last_seq,
                is_final: false,
                audio: active.audio.clone(),
            });
        }
        None
    }

    /// Close the current utterance now (pause/stop). Returns it if it contained real speech.
    pub fn flush(&mut self) -> Option<Utterance> {
        self.pre_roll.clear();
        self.finish()
    }

    fn finish(&mut self) -> Option<Utterance> {
        let a = self.active.take()?;
        if a.speech_ms < self.cfg.min_speech_ms {
            return None;
        }
        Some(Utterance {
            id: a.id,
            start_seq: a.start_seq,
            end_seq: a.last_seq,
            is_final: true,
            audio: a.audio,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const N: usize = 320;

    fn silence() -> Vec<i16> {
        vec![0; N]
    }

    /// A 300 Hz tone at the given dBFS (RMS).
    fn tone(dbfs: f32, seq: u64) -> Vec<i16> {
        let amp = 10f32.powf(dbfs / 20.0) * std::f32::consts::SQRT_2;
        (0..N)
            .map(|i| {
                let t = (seq as usize * N + i) as f32 / SAMPLE_RATE as f32;
                (amp * (2.0 * std::f32::consts::PI * 300.0 * t).sin() * 32767.0) as i16
            })
            .collect()
    }

    fn run(
        seg: &mut Segmenter,
        frames: impl IntoIterator<Item = Vec<i16>>,
        start: u64,
    ) -> (Vec<Utterance>, u64) {
        let mut out = Vec::new();
        let mut seq = start;
        for f in frames {
            if let Some(u) = seg.push(seq, &f) {
                out.push(u);
            }
            seq += 1;
        }
        (out, seq)
    }

    #[test]
    fn silence_never_reaches_the_model() {
        let mut s = Segmenter::new(SegmenterConfig::default());
        let (out, _) = run(&mut s, (0..2_000).map(|_| silence()), 0);
        assert!(out.is_empty());
        assert!(s.flush().is_none());
    }

    #[test]
    fn speech_then_pause_gives_partials_then_one_final() {
        let mut s = Segmenter::new(SegmenterConfig::default());
        let (mut out, seq) = run(&mut s, (0..50).map(|_| silence()), 0);
        let (o, seq) = run(&mut s, (50..250).map(|i| tone(-20.0, i)), seq); // 4 s speech
        out.extend(o);
        let (o, _) = run(&mut s, (0..50).map(|_| silence()), seq); // 1 s silence
        out.extend(o);

        let partials: Vec<_> = out.iter().filter(|u| !u.is_final).collect();
        let finals: Vec<_> = out.iter().filter(|u| u.is_final).collect();
        assert_eq!(finals.len(), 1);
        assert!(
            partials.len() >= 2,
            "partials while speaking: {}",
            partials.len()
        );
        assert!(out.iter().all(|u| u.id == 0), "one utterance id");
        let f = finals[0];
        // Pre-roll of 240 ms = 12 frames before speech started at seq 50.
        assert_eq!(f.start_seq, 38);
        assert_eq!(f.start_ms(), 760);
        // Final after 700 ms of trailing silence.
        assert_eq!(f.end_seq, 250 + 35 - 1);
        assert!(partials
            .windows(2)
            .all(|w| w[1].audio.len() > w[0].audio.len()));
    }

    #[test]
    fn long_monologue_is_split() {
        let mut s = Segmenter::new(SegmenterConfig::default());
        let (out, _) = run(&mut s, (0..2_000).map(|i| tone(-20.0, i)), 0); // 40 s
        let finals: Vec<_> = out.iter().filter(|u| u.is_final).collect();
        assert_eq!(finals.len(), 2, "15 s chunks");
        assert!(finals.iter().all(|u| u.duration_ms() <= 15_000));
        assert_ne!(finals[0].id, finals[1].id);
        let rest = s.flush().unwrap();
        assert!(rest.is_final && rest.duration_ms() > 9_000);
    }

    #[test]
    fn short_clicks_are_discarded() {
        let mut s = Segmenter::new(SegmenterConfig::default());
        let (mut out, seq) = run(&mut s, (0..5).map(|i| tone(-15.0, i)), 0); // 100 ms
        let (o, _) = run(&mut s, (0..60).map(|_| silence()), seq);
        out.extend(o);
        assert!(out.is_empty());
    }

    #[test]
    fn noise_floor_adapts_to_a_noisy_room() {
        let mut s = Segmenter::new(SegmenterConfig::default());
        // Constant -40 dBFS hum for 10 s: louder than the fixed threshold, but it is noise.
        let mut out = Vec::new();
        for i in 0..500u64 {
            if let Some(u) = s.push(i, &tone(-40.0, i)) {
                out.push(u);
            }
        }
        // The first frames may open an utterance before the floor adapts; afterwards the
        // hum alone must not keep producing speech.
        let late = out.iter().filter(|u| u.start_seq > 200).count();
        assert_eq!(late, 0, "steady background noise is not speech");
    }

    #[test]
    fn flush_finishes_an_utterance_on_pause() {
        let mut s = Segmenter::new(SegmenterConfig::default());
        let (_, _) = run(&mut s, (0..40).map(|i| tone(-20.0, i)), 0);
        assert!(s.is_in_utterance());
        let u = s.flush().unwrap();
        assert!(u.is_final);
        assert_eq!(u.end_seq, 39);
        assert!(!s.is_in_utterance());
    }

    #[test]
    fn dbfs_of_known_signals() {
        assert_eq!(frame_dbfs(&silence()), -100.0);
        let db = frame_dbfs(&tone(-20.0, 0));
        assert!((db - -20.0).abs() < 0.5, "{db}");
    }
}
