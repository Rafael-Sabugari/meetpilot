//! Command-line probe to validate native capture on a real Windows/macOS machine.
//!
//!   mp-audio-probe capabilities
//!   mp-audio-probe devices
//!   mp-audio-probe capture [--system] [--mic] [--seconds N] [--wav out.wav]
//!
//! `--wav` is an explicit, opt-in developer aid that writes the *normalized* 16 kHz mono
//! audio to a local file so you can listen to what would be sent to speech-to-text.
//! Nothing is written unless you pass it. Nothing is sent over the network.

use std::io::Write;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use meetpilot_audio::{
    default_provider, AudioSource, CaptureConfig, CaptureSession, SessionEvent, SourceSelection,
    TARGET_SAMPLE_RATE,
};

fn usage() -> ! {
    eprintln!("usage: mp-audio-probe <capabilities|devices|capture> [--system] [--mic] [--seconds N] [--wav PATH]");
    std::process::exit(2)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(cmd) = args.first() else { usage() };
    let Some(provider) = default_provider() else {
        eprintln!("No native capture backend on this platform/build.");
        std::process::exit(1);
    };

    match cmd.as_str() {
        "capabilities" => {
            let caps = provider.capabilities();
            println!("backend: {}", provider.backend_name());
            println!("{caps:#?}");
        }
        "devices" => {
            for (label, list) in [
                (
                    "Output devices (system audio / loopback)",
                    provider.list_output_devices(),
                ),
                ("Input devices (microphone)", provider.list_input_devices()),
            ] {
                println!("{label}:");
                match list {
                    Ok(devs) => {
                        for d in devs {
                            println!(
                                "  {} {}  [{}]",
                                if d.is_default { "*" } else { " " },
                                d.name,
                                d.id
                            );
                        }
                    }
                    Err(e) => println!("  error: {e}"),
                }
            }
        }
        "capture" => capture(provider, &args[1..]),
        _ => usage(),
    }
}

fn capture(provider: Arc<dyn meetpilot_audio::AudioCaptureProvider>, args: &[String]) {
    let mut config = CaptureConfig::default();
    let mut seconds = 10u64;
    let mut wav: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--system" => config.system_audio = Some(SourceSelection::default()),
            "--mic" => config.microphone = Some(SourceSelection::default()),
            "--seconds" => {
                seconds = it
                    .next()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or_else(|| usage())
            }
            "--wav" => wav = Some(it.next().cloned().unwrap_or_else(|| usage())),
            _ => usage(),
        }
    }
    if config.system_audio.is_none() && config.microphone.is_none() {
        config.system_audio = Some(SourceSelection::default());
    }

    let recorded: Arc<Mutex<Vec<i16>>> = Arc::new(Mutex::new(Vec::new()));
    let keep = wav.is_some();
    // The WAV holds one source: system audio if selected, otherwise the microphone.
    let wav_source = if config.system_audio.is_some() {
        AudioSource::System
    } else {
        AudioSource::Microphone
    };
    let rec = Arc::clone(&recorded);
    let handler = Arc::new(move |ev: SessionEvent| match ev {
        SessionEvent::Level(l) => {
            let bars = (((l.rms_dbfs + 60.0) / 60.0).clamp(0.0, 1.0) * 30.0) as usize;
            let label = match l.source {
                AudioSource::System => "system",
                AudioSource::Microphone => "mic   ",
            };
            print!(
                "\r{label} {:>6.1} dBFS |{:<30}|",
                l.rms_dbfs,
                "#".repeat(bars)
            );
            let _ = std::io::stdout().flush();
        }
        SessionEvent::Frame(f) => {
            if keep && f.source == wav_source {
                rec.lock().unwrap().extend_from_slice(&f.samples);
            }
        }
        SessionEvent::Status(s) => {
            for src in &s.sources {
                eprintln!("\n[status] {:?}: {:?}", src.source, src.state);
            }
        }
        SessionEvent::Error(e) => eprintln!("\n[error] {e}"),
    });

    let session = CaptureSession::new(provider, handler);
    println!("● CAPTURING for {seconds}s (Ctrl+C to abort). No audio leaves this computer.");
    if let Err(e) = session.start(&config) {
        eprintln!("failed to start: {e}");
        std::process::exit(1);
    }
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(seconds) {
        std::thread::sleep(Duration::from_millis(100));
    }
    let status = session.stop();
    println!("\n■ stopped");
    for s in &status.sources {
        println!(
            "{:?}: device={:?} native={:?} frames={} captured={}ms dropped_chunks={} reconnects={}",
            s.source,
            s.device_name,
            s.native_format,
            s.frames_emitted,
            s.captured_ms,
            s.dropped_chunks,
            s.reconnects
        );
    }
    if let Some(path) = wav {
        let samples = recorded.lock().unwrap();
        match write_wav(&path, &samples) {
            Ok(()) => println!(
                "wrote {} samples to {path} (16 kHz mono, {wav_source})",
                samples.len()
            ),
            Err(e) => eprintln!("failed to write {path}: {e}"),
        }
    }
}

fn write_wav(path: &str, samples: &[i16]) -> std::io::Result<()> {
    let mut f = std::fs::File::create(path)?;
    let data_len = (samples.len() * 2) as u32;
    f.write_all(b"RIFF")?;
    f.write_all(&(36 + data_len).to_le_bytes())?;
    f.write_all(b"WAVEfmt ")?;
    f.write_all(&16u32.to_le_bytes())?;
    f.write_all(&1u16.to_le_bytes())?; // PCM
    f.write_all(&1u16.to_le_bytes())?; // mono
    f.write_all(&TARGET_SAMPLE_RATE.to_le_bytes())?;
    f.write_all(&(TARGET_SAMPLE_RATE * 2).to_le_bytes())?;
    f.write_all(&2u16.to_le_bytes())?;
    f.write_all(&16u16.to_le_bytes())?;
    f.write_all(b"data")?;
    f.write_all(&data_len.to_le_bytes())?;
    for s in samples {
        f.write_all(&s.to_le_bytes())?;
    }
    Ok(())
}
