//! Real Whisper inference. Needs a model and a WAV file, so it is opt-in:
//!
//!   MEETPILOT_TEST_WHISPER_MODEL=ggml-tiny.bin MEETPILOT_TEST_WAV=jfk.wav \
//!     cargo test -p meetpilot-local-stt --features whisper --test real_model -- --nocapture
//!
//! CI downloads ggml-tiny.bin (verified) and whisper.cpp's samples/jfk.wav.
#![cfg(feature = "whisper")]

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use meetpilot_local_stt::whisper::WhisperModel;
use meetpilot_local_stt::{EngineConfig, LocalEvent, LocalTranscriber, Source};

fn inputs() -> Option<(String, Vec<i16>)> {
    let model = std::env::var("MEETPILOT_TEST_WHISPER_MODEL").ok()?;
    let wav = std::env::var("MEETPILOT_TEST_WAV").ok()?;
    let bytes = std::fs::read(wav).expect("read wav");
    // Minimal WAV parsing: find the "data" chunk of a 16 kHz mono PCM16 file.
    assert_eq!(&bytes[0..4], b"RIFF");
    assert_eq!(
        u32::from_le_bytes(bytes[24..28].try_into().unwrap()),
        16_000,
        "16 kHz"
    );
    assert_eq!(
        u16::from_le_bytes(bytes[22..24].try_into().unwrap()),
        1,
        "mono"
    );
    let mut i = 12;
    while i + 8 <= bytes.len() {
        let id = &bytes[i..i + 4];
        let len = u32::from_le_bytes(bytes[i + 4..i + 8].try_into().unwrap()) as usize;
        if id == b"data" {
            let pcm = bytes[i + 8..i + 8 + len]
                .chunks_exact(2)
                .map(|c| i16::from_le_bytes([c[0], c[1]]))
                .collect();
            return Some((model, pcm));
        }
        i += 8 + len + (len % 2);
    }
    panic!("no data chunk");
}

#[test]
fn whisper_transcribes_known_speech_through_the_streaming_engine() {
    let Some((model, pcm)) = inputs() else {
        // CI sets MEETPILOT_REQUIRE_REAL_MODEL so a missing model fails instead of skipping.
        assert!(
            std::env::var("MEETPILOT_REQUIRE_REAL_MODEL").is_err(),
            "MEETPILOT_REQUIRE_REAL_MODEL is set but no model/WAV was provided"
        );
        eprintln!("skipped: set MEETPILOT_TEST_WHISPER_MODEL and MEETPILOT_TEST_WAV");
        return;
    };
    let events = Arc::new(Mutex::new(Vec::new()));
    let e2 = Arc::clone(&events);
    let t = LocalTranscriber::start(
        // With a vocabulary prompt, as the app sends it in free mode.
        WhisperModel::factory_with_prompt(
            model.into(),
            Some("Salesforce, Apex, LWC, SOQL, Trigger.new.".to_string()),
        ),
        EngineConfig::for_language("en"),
        Arc::new(move |e| e2.lock().unwrap().push(e)),
    );
    let started = Instant::now();
    // Feed 20 ms frames as the capture pipeline would (faster than real time).
    for (seq, frame) in pcm.chunks(320).enumerate() {
        if frame.len() == 320 {
            t.push_frame(Source::System, seq as u64, frame);
        }
    }
    assert!(t.stop(Duration::from_secs(120)), "transcription finished");
    let elapsed = started.elapsed();

    let events = events.lock().unwrap();
    assert!(
        matches!(events.first(), Some(LocalEvent::Ready)),
        "{events:?}"
    );
    let text: String = events
        .iter()
        .filter_map(|e| match e {
            LocalEvent::Segment(s) if s.is_final => Some(s.text.clone()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join(" ");
    eprintln!(
        "transcript ({} s audio, {:?}): {text}",
        pcm.len() / 16_000,
        elapsed
    );
    let lower = text.to_lowercase();
    assert!(lower.contains("country"), "unexpected transcript: {text}");
    assert!(
        lower.contains("ask not") || lower.contains("ask"),
        "unexpected transcript: {text}"
    );
}
