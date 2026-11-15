//! Small, dependency-free DSP used to normalize native audio to 16 kHz mono PCM16.

use crate::types::SAMPLES_PER_FRAME;

/// Average interleaved channels into mono.
pub fn downmix_to_mono(interleaved: &[f32], channels: u16) -> Vec<f32> {
    let ch = channels.max(1) as usize;
    if ch == 1 {
        return interleaved.to_vec();
    }
    let inv = 1.0 / ch as f32;
    interleaved
        .chunks_exact(ch)
        .map(|frame| frame.iter().sum::<f32>() * inv)
        .collect()
}

pub fn f32_to_i16(x: f32) -> i16 {
    let v = if x.is_finite() {
        x.clamp(-1.0, 1.0)
    } else {
        0.0
    };
    (v * i16::MAX as f32).round() as i16
}

/// (rms_dbfs, peak) for a block of samples in `[-1, 1]`. Silence reports -100 dBFS.
pub fn levels(samples: &[f32]) -> (f32, f32) {
    if samples.is_empty() {
        return (-100.0, 0.0);
    }
    let mut sum = 0.0f64;
    let mut peak = 0.0f32;
    for &s in samples {
        let s = if s.is_finite() { s } else { 0.0 };
        sum += (s as f64) * (s as f64);
        peak = peak.max(s.abs());
    }
    let rms = (sum / samples.len() as f64).sqrt();
    let db = if rms <= 1e-5 {
        -100.0
    } else {
        (20.0 * rms.log10()) as f32
    };
    (db.max(-100.0), peak.min(1.0))
}

#[derive(Debug, Clone, Copy)]
struct Biquad {
    b0: f32,
    b1: f32,
    b2: f32,
    a1: f32,
    a2: f32,
    z1: f32,
    z2: f32,
}

impl Biquad {
    /// RBJ cookbook low-pass.
    fn lowpass(sample_rate: f32, cutoff: f32, q: f32) -> Self {
        let w0 = 2.0 * std::f32::consts::PI * cutoff / sample_rate;
        let (sin, cos) = w0.sin_cos();
        let alpha = sin / (2.0 * q);
        let a0 = 1.0 + alpha;
        Self {
            b0: ((1.0 - cos) / 2.0) / a0,
            b1: (1.0 - cos) / a0,
            b2: ((1.0 - cos) / 2.0) / a0,
            a1: (-2.0 * cos) / a0,
            a2: (1.0 - alpha) / a0,
            z1: 0.0,
            z2: 0.0,
        }
    }

    #[inline]
    fn process(&mut self, x: f32) -> f32 {
        // Transposed direct form II.
        let y = self.b0 * x + self.z1;
        self.z1 = self.b1 * x - self.a1 * y + self.z2;
        self.z2 = self.b2 * x - self.a2 * y;
        y
    }
}

/// Streaming mono resampler: 4th-order Butterworth anti-alias filter + linear interpolation.
///
/// Adequate for speech-to-text (content is band-limited below ~7 kHz); state is carried
/// across chunks so chunk boundaries do not produce clicks.
#[derive(Debug, Clone)]
pub struct StreamingResampler {
    in_rate: u32,
    out_rate: u32,
    step: f64,
    /// Read position relative to the start of the next input chunk; -1.0 refers to `last`.
    t: f64,
    last: f32,
    filters: Vec<Biquad>,
}

impl StreamingResampler {
    pub fn new(in_rate: u32, out_rate: u32) -> Self {
        assert!(in_rate > 0 && out_rate > 0, "sample rates must be positive");
        let filters = if in_rate > out_rate {
            let fc = 0.45 * out_rate as f32;
            // Butterworth 4th order = two biquads with these Qs.
            vec![
                Biquad::lowpass(in_rate as f32, fc, 0.541_196_1),
                Biquad::lowpass(in_rate as f32, fc, 1.306_563),
            ]
        } else {
            Vec::new()
        };
        Self {
            in_rate,
            out_rate,
            step: in_rate as f64 / out_rate as f64,
            t: 0.0,
            last: 0.0,
            filters,
        }
    }

    pub fn in_rate(&self) -> u32 {
        self.in_rate
    }

    pub fn process(&mut self, input: &[f32]) -> Vec<f32> {
        if self.in_rate == self.out_rate {
            return input.to_vec();
        }
        if input.is_empty() {
            return Vec::new();
        }
        let filtered: Vec<f32> = if self.filters.is_empty() {
            input.to_vec()
        } else {
            input
                .iter()
                .map(|&x| self.filters.iter_mut().fold(x, |acc, f| f.process(acc)))
                .collect()
        };
        let n = filtered.len();
        let last_idx = (n - 1) as f64;
        let mut out = Vec::with_capacity((n as f64 / self.step).ceil() as usize + 1);
        while self.t <= last_idx {
            let i0 = self.t.floor();
            let frac = (self.t - i0) as f32;
            let a = if i0 < 0.0 {
                self.last
            } else {
                filtered[i0 as usize]
            };
            let b_idx = ((i0 + 1.0) as usize).min(n - 1);
            let b = filtered[b_idx];
            out.push(a + (b - a) * frac);
            self.t += self.step;
        }
        self.t -= n as f64;
        self.last = filtered[n - 1];
        out
    }
}

/// Accumulates PCM16 into fixed-size frames.
#[derive(Debug, Default, Clone)]
pub struct Framer {
    pending: Vec<i16>,
}

impl Framer {
    pub fn push(&mut self, samples: &[i16]) -> Vec<Vec<i16>> {
        self.pending.extend_from_slice(samples);
        let mut frames = Vec::new();
        while self.pending.len() >= SAMPLES_PER_FRAME {
            let rest = self.pending.split_off(SAMPLES_PER_FRAME);
            frames.push(std::mem::replace(&mut self.pending, rest));
        }
        frames
    }

    pub fn clear(&mut self) {
        self.pending.clear();
    }

    pub fn pending_len(&self) -> usize {
        self.pending.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(freq: f32, rate: u32, secs: f32, amp: f32) -> Vec<f32> {
        let n = (rate as f32 * secs) as usize;
        (0..n)
            .map(|i| amp * (2.0 * std::f32::consts::PI * freq * i as f32 / rate as f32).sin())
            .collect()
    }

    #[test]
    fn downmix_averages_channels() {
        let st = [1.0, 0.0, 0.5, 0.5, -1.0, 1.0];
        assert_eq!(downmix_to_mono(&st, 2), vec![0.5, 0.5, 0.0]);
        assert_eq!(downmix_to_mono(&[0.1, 0.2], 1), vec![0.1, 0.2]);
    }

    #[test]
    fn downmix_ignores_trailing_partial_frame() {
        assert_eq!(downmix_to_mono(&[1.0, 1.0, 0.3], 2), vec![1.0]);
    }

    #[test]
    fn f32_to_i16_clamps_and_handles_nan() {
        assert_eq!(f32_to_i16(1.5), i16::MAX);
        assert_eq!(f32_to_i16(-1.5), -i16::MAX);
        assert_eq!(f32_to_i16(f32::NAN), 0);
        assert_eq!(f32_to_i16(0.0), 0);
    }

    #[test]
    fn levels_of_silence_and_full_scale_sine() {
        assert_eq!(levels(&[0.0; 100]).0, -100.0);
        let (db, peak) = levels(&sine(1000.0, 48_000, 0.1, 1.0));
        assert!((db - -3.01).abs() < 0.1, "db={db}");
        assert!(peak > 0.99);
    }

    #[test]
    fn resampler_preserves_speech_band_tone() {
        let mut r = StreamingResampler::new(48_000, 16_000);
        let out = r.process(&sine(1000.0, 48_000, 1.0, 0.5));
        assert!((out.len() as i64 - 16_000).abs() <= 2, "len={}", out.len());
        // Skip filter warm-up.
        let (db, _) = levels(&out[1600..]);
        let expected = 20.0 * (0.5f32 / 2f32.sqrt()).log10();
        assert!((db - expected).abs() < 0.5, "db={db} expected={expected}");
    }

    #[test]
    fn resampler_attenuates_content_above_nyquist() {
        let mut r = StreamingResampler::new(48_000, 16_000);
        let out = r.process(&sine(15_000.0, 48_000, 1.0, 0.5));
        let (db, _) = levels(&out[1600..]);
        assert!(db < -35.0, "aliased energy too high: {db} dBFS");
    }

    #[test]
    fn resampler_is_chunk_boundary_invariant() {
        let input = sine(440.0, 44_100, 0.5, 0.8);
        let mut whole = StreamingResampler::new(44_100, 16_000);
        let a = whole.process(&input);
        let mut chunked = StreamingResampler::new(44_100, 16_000);
        let mut b = Vec::new();
        for c in input.chunks(441) {
            b.extend(chunked.process(c));
        }
        assert_eq!(a.len(), b.len());
        for (x, y) in a.iter().zip(&b) {
            assert!((x - y).abs() < 1e-4);
        }
    }

    #[test]
    fn resampler_upsamples_8k() {
        let mut r = StreamingResampler::new(8_000, 16_000);
        let out = r.process(&sine(300.0, 8_000, 1.0, 0.5));
        assert!((out.len() as i64 - 16_000).abs() <= 2);
    }

    #[test]
    fn passthrough_when_rates_match() {
        let mut r = StreamingResampler::new(16_000, 16_000);
        assert_eq!(r.process(&[0.1, 0.2]), vec![0.1, 0.2]);
    }

    #[test]
    fn framer_emits_fixed_frames_and_keeps_remainder() {
        let mut f = Framer::default();
        assert!(f.push(&[1; 100]).is_empty());
        let frames = f.push(&[2; 600]);
        assert_eq!(frames.len(), 2);
        assert!(frames.iter().all(|fr| fr.len() == SAMPLES_PER_FRAME));
        assert_eq!(f.pending_len(), 700 - 2 * SAMPLES_PER_FRAME);
        f.clear();
        assert_eq!(f.pending_len(), 0);
    }
}
