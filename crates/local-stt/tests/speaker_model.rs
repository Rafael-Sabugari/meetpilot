//! Real speaker model check (opt-in): MEETPILOT_TEST_SPEAKER_MODEL=/path/to/model.onnx
#![cfg(feature = "diarization")]

use meetpilot_local_stt::speakers::sherpa::factory;

fn voice(f0: f32, seconds: f32, seed: u32) -> Vec<f32> {
    // A crude voiced sound: harmonics of f0 with a slow vibrato and a little noise.
    let n = (seconds * 16_000.0) as usize;
    let mut x = seed;
    (0..n)
        .map(|i| {
            let t = i as f32 / 16_000.0;
            let f = f0 * (1.0 + 0.02 * (2.0 * std::f32::consts::PI * 5.0 * t).sin());
            let mut s = 0.0;
            for h in 1..12 {
                s += (2.0 * std::f32::consts::PI * f * h as f32 * t).sin() / h as f32;
            }
            x = x.wrapping_mul(1_103_515_245).wrapping_add(12_345);
            s * 0.2 + ((x >> 16) as f32 / 65_536.0 - 0.5) * 0.01
        })
        .collect()
}

fn cos(a: &[f32], b: &[f32]) -> f32 {
    let d: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    d / (a.iter().map(|x| x * x).sum::<f32>().sqrt() * b.iter().map(|x| x * x).sum::<f32>().sqrt())
}

#[test]
fn real_model_gives_stable_embeddings() {
    let Ok(path) = std::env::var("MEETPILOT_TEST_SPEAKER_MODEL") else {
        eprintln!("skipped: set MEETPILOT_TEST_SPEAKER_MODEL");
        return;
    };
    let mut e = factory(path.into())().expect("model loads");
    let a1 = e.embed(&voice(110.0, 2.0, 1)).unwrap();
    let a2 = e.embed(&voice(110.0, 2.5, 7)).unwrap();
    let b = e.embed(&voice(240.0, 2.0, 3)).unwrap();
    assert!(a1.len() >= 128, "embedding size {}", a1.len());
    let same = cos(&a1, &a2);
    let other = cos(&a1, &b);
    eprintln!("same voice {same:.3} · other voice {other:.3}");
    assert!(
        same > other,
        "the same sound should be closer than a different one"
    );
}
