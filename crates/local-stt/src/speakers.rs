//! Who is speaking among the others ("Pessoa 1", "Pessoa 2"…), on this computer.
//!
//! Each finished phrase from the meeting audio gets a voice embedding (a short vector that
//! describes the voice, not the words); phrases whose voices are close are grouped under the
//! same label. Only the label leaves this module: no audio and no voice prints are stored.

use crate::models::ModelSpec;

/// Voice model (CAM++ from 3D-Speaker, trained on VoxCeleb), from sherpa-onnx's releases.
pub const SPEAKER_MODEL: ModelSpec = ModelSpec {
    id: "speaker-campplus-en",
    file: "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
    approx_bytes: 29_596_979,
    sha1: "80b5015fe4c4becbb73993ca087e4ee58421a8fa",
    listed: false,
    url: Some(
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
    ),
};

/// Turns 16 kHz mono audio into a voice embedding.
pub trait SpeakerEmbedder: Send {
    fn embed(&mut self, audio: &[f32]) -> Result<Vec<f32>, String>;
}

pub type SpeakerEmbedderFactory =
    Box<dyn FnOnce() -> Result<Box<dyn SpeakerEmbedder>, String> + Send>;

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let (mut dot, mut na, mut nb) = (0f32, 0f32, 0f32);
    for (x, y) in a.iter().zip(b) {
        dot += x * y;
        na += x * x;
        nb += y * y;
    }
    if na <= 0.0 || nb <= 0.0 {
        0.0
    } else {
        dot / (na.sqrt() * nb.sqrt())
    }
}

/// Online grouping of voices: each new voice joins the closest known speaker when similar
/// enough, otherwise it becomes a new one.
#[derive(Debug, Clone)]
pub struct SpeakerClusters {
    centroids: Vec<(Vec<f32>, u32)>,
    pub threshold: f32,
    pub max_speakers: usize,
}

impl Default for SpeakerClusters {
    fn default() -> Self {
        Self {
            centroids: Vec::new(),
            // CAM++ same-speaker similarity is usually above ~0.5 on clean speech; meeting
            // audio is noisier, so be a bit more permissive to avoid splitting one person.
            threshold: 0.45,
            max_speakers: 8,
        }
    }
}

impl SpeakerClusters {
    /// Index of the speaker for this embedding (0-based).
    pub fn assign(&mut self, embedding: &[f32]) -> usize {
        let best = self
            .centroids
            .iter()
            .enumerate()
            .map(|(i, (c, n))| {
                let mean: Vec<f32> = c.iter().map(|x| x / *n as f32).collect();
                (i, cosine(&mean, embedding))
            })
            .max_by(|a, b| a.1.total_cmp(&b.1));
        match best {
            Some((i, sim))
                if sim >= self.threshold || self.centroids.len() >= self.max_speakers =>
            {
                let (c, n) = &mut self.centroids[i];
                for (x, y) in c.iter_mut().zip(embedding) {
                    *x += y;
                }
                *n += 1;
                i
            }
            _ => {
                self.centroids.push((embedding.to_vec(), 1));
                self.centroids.len() - 1
            }
        }
    }

    pub fn len(&self) -> usize {
        self.centroids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.centroids.is_empty()
    }
}

/// Phrases shorter than this carry too little voice to tell people apart.
pub const MIN_SECONDS: f32 = 1.0;
/// A short phrase right after someone spoke is most likely the same person.
const CONTINUE_MS: u64 = 3_000;

pub struct Diarizer {
    embedder: Box<dyn SpeakerEmbedder>,
    clusters: SpeakerClusters,
    last: Option<(usize, u64)>,
}

pub fn label(index: usize) -> String {
    format!("Pessoa {}", index + 1)
}

impl Diarizer {
    pub fn new(embedder: Box<dyn SpeakerEmbedder>) -> Self {
        Self {
            embedder,
            clusters: SpeakerClusters::default(),
            last: None,
        }
    }

    /// Label for one finished phrase of the others (`start_ms`/`end_ms` on the meeting
    /// clock), or None when it cannot tell.
    pub fn speaker(&mut self, audio: &[f32], start_ms: u64, end_ms: u64) -> Option<String> {
        let seconds = audio.len() as f32 / 16_000.0;
        if seconds < MIN_SECONDS {
            return match self.last {
                Some((i, last_end)) if start_ms.saturating_sub(last_end) <= CONTINUE_MS => {
                    self.last = Some((i, end_ms));
                    Some(label(i))
                }
                _ => None,
            };
        }
        let embedding = self.embedder.embed(audio).ok()?;
        let i = self.clusters.assign(&embedding);
        self.last = Some((i, end_ms));
        Some(label(i))
    }
}

#[cfg(feature = "diarization")]
pub mod sherpa {
    use super::{SpeakerEmbedder, SpeakerEmbedderFactory};
    use sherpa_rs::speaker_id::{EmbeddingExtractor, ExtractorConfig};

    pub struct SherpaEmbedder(EmbeddingExtractor);

    impl SpeakerEmbedder for SherpaEmbedder {
        fn embed(&mut self, audio: &[f32]) -> Result<Vec<f32>, String> {
            self.0
                .compute_speaker_embedding(audio.to_vec(), 16_000)
                .map_err(|e| e.to_string())
        }
    }

    pub fn factory(model: std::path::PathBuf) -> SpeakerEmbedderFactory {
        Box::new(move || {
            let config = ExtractorConfig {
                model: model.to_string_lossy().into_owned(),
                num_threads: Some(2),
                ..Default::default()
            };
            EmbeddingExtractor::new(config)
                .map(|e| Box::new(SherpaEmbedder(e)) as Box<dyn SpeakerEmbedder>)
                .map_err(|e| format!("Speaker model: {e}"))
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Voices as fixed directions, with a little per-phrase variation.
    struct FakeVoices;
    impl SpeakerEmbedder for FakeVoices {
        fn embed(&mut self, audio: &[f32]) -> Result<Vec<f32>, String> {
            // The first sample encodes "who" (test only), the second a small variation.
            let who = audio[0].round() as usize;
            let mut v = vec![0.05f32; 8];
            v[who] = 1.0;
            v[(who + 1) % 8] += audio[1];
            Ok(v)
        }
    }

    fn phrase(who: usize, wobble: f32, seconds: f32) -> Vec<f32> {
        let mut a = vec![0.0; (seconds * 16_000.0) as usize];
        a[0] = who as f32;
        a[1] = wobble;
        a
    }

    #[test]
    fn same_voice_keeps_its_label_and_new_voices_get_new_ones() {
        let mut d = Diarizer::new(Box::new(FakeVoices));
        assert_eq!(
            d.speaker(&phrase(2, 0.1, 2.0), 0, 2_000).as_deref(),
            Some("Pessoa 1")
        );
        assert_eq!(
            d.speaker(&phrase(5, 0.0, 3.0), 3_000, 6_000).as_deref(),
            Some("Pessoa 2")
        );
        assert_eq!(
            d.speaker(&phrase(2, 0.3, 1.5), 7_000, 8_500).as_deref(),
            Some("Pessoa 1")
        );
        // A short "Sim." right after keeps the previous speaker; a lone one later is unknown.
        assert_eq!(
            d.speaker(&phrase(5, 0.0, 0.4), 9_000, 9_400).as_deref(),
            Some("Pessoa 1")
        );
        assert_eq!(d.speaker(&phrase(5, 0.0, 0.4), 60_000, 60_400), None);
    }

    #[test]
    fn caps_the_number_of_speakers() {
        let mut c = SpeakerClusters {
            max_speakers: 2,
            ..Default::default()
        };
        let v = |i: usize| {
            let mut v = vec![0.0f32; 4];
            v[i] = 1.0;
            v
        };
        assert_eq!(c.assign(&v(0)), 0);
        assert_eq!(c.assign(&v(1)), 1);
        assert!(
            c.assign(&v(2)) < 2,
            "joins the closest instead of a third speaker"
        );
        assert_eq!(c.len(), 2);
    }
}
