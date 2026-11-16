//! Whisper backend (whisper.cpp via `whisper-rs`). Enabled with the `whisper` feature.

use std::path::Path;
use std::sync::{Arc, Once};

use whisper_rs::{
    FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters, WhisperState,
};

use crate::engine::{Recognizer, RecognizerFactory};

/// Segments Whisper itself flags as probably not speech are dropped.
const NO_SPEECH_THRESHOLD: f32 = 0.6;

static LOG_HOOKS: Once = Once::new();

/// A loaded model, shareable across recognizers.
#[derive(Clone)]
pub struct WhisperModel {
    ctx: Arc<WhisperContext>,
}

impl WhisperModel {
    pub fn load(path: &Path) -> Result<Self, String> {
        // Route whisper.cpp's console output away from stdout/stderr.
        LOG_HOOKS.call_once(whisper_rs::install_logging_hooks);
        let mut params = WhisperContextParameters::default();
        params.use_gpu(cfg!(feature = "metal"));
        // Much faster attention on the Apple GPU.
        params.flash_attn(cfg!(feature = "metal"));
        let ctx = WhisperContext::new_with_params(path, params)
            .map_err(|e| format!("could not load the Whisper model: {e}"))?;
        Ok(Self { ctx: Arc::new(ctx) })
    }

    pub fn recognizer(&self) -> Result<Box<dyn Recognizer>, String> {
        let state = self
            .ctx
            .create_state()
            .map_err(|e| format!("could not initialise Whisper: {e}"))?;
        let threads = std::thread::available_parallelism()
            .map(|n| n.get().saturating_sub(1).clamp(1, 8))
            .unwrap_or(4) as i32;
        Ok(Box::new(WhisperRecognizer { state, threads }))
    }

    /// Factory that loads the model on the engine's worker thread.
    pub fn factory(path: std::path::PathBuf) -> RecognizerFactory {
        Box::new(move || WhisperModel::load(&path)?.recognizer())
    }
}

/// Encoder context for `samples` of 16 kHz audio: 50 positions per second (1500 = 30 s),
/// plus a margin, never below a floor that keeps short phrases accurate.
pub fn audio_ctx(samples: usize) -> i32 {
    const FULL: usize = 1500;
    const FLOOR: usize = 384;
    const MARGIN: usize = 128;
    let needed = samples.div_ceil(16_000 / 50) + MARGIN;
    needed.clamp(FLOOR, FULL) as i32
}

struct WhisperRecognizer {
    state: WhisperState,
    threads: i32,
}

impl Recognizer for WhisperRecognizer {
    fn transcribe(&mut self, audio: &[f32], language: &str) -> Result<String, String> {
        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_language(Some(language));
        params.set_n_threads(self.threads);
        params.set_translate(false);
        // Each utterance is independent: avoids repetition loops carried across chunks.
        params.set_no_context(true);
        params.set_suppress_blank(true);
        params.set_suppress_nst(true);
        params.set_temperature(0.0);
        params.set_no_speech_thold(NO_SPEECH_THRESHOLD);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_special(false);
        params.set_print_timestamps(false);
        // One utterance at a time: no timestamps or segment splitting needed.
        params.set_no_timestamps(true);
        params.set_single_segment(true);
        // Whisper encodes a fixed 30 s window by default; size it to the utterance instead.
        params.set_audio_ctx(audio_ctx(audio.len()));
        self.state
            .full(params, audio)
            .map_err(|e| format!("transcription failed: {e}"))?;
        let mut text = String::new();
        for seg in self.state.as_iter() {
            if seg.no_speech_probability() > NO_SPEECH_THRESHOLD {
                continue;
            }
            if let Ok(s) = seg.to_str_lossy() {
                text.push_str(&s);
            }
        }
        Ok(text.trim().to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::audio_ctx;

    #[test]
    fn encoder_window_follows_the_utterance_length() {
        assert_eq!(audio_ctx(16_000), 384, "1 s phrase: floor");
        assert_eq!(audio_ctx(16_000 * 10), 628, "10 s: 500 + margin");
        assert_eq!(audio_ctx(16_000 * 30), 1500, "30 s: full window");
        assert_eq!(
            audio_ctx(16_000 * 60),
            1500,
            "never above the model's window"
        );
    }
}
