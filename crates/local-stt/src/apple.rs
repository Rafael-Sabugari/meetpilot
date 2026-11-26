//! Apple's on-device transcription (SpeechAnalyzer, macOS 26+) through the small
//! `meetpilot-speech` helper that ships next to the app. Audio is piped to the helper and
//! only text comes back; nothing is written to disk.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use serde::Deserialize;

use crate::engine::{Recognizer, RecognizerFactory};

#[derive(Debug, Deserialize)]
struct Reply {
    #[serde(default)]
    ready: bool,
    #[serde(default)]
    text: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

pub struct AppleSpeech {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

/// BCP-47 locale for the helper from a Whisper language code ("pt", "en", "auto").
pub fn locale_for(whisper_language: &str) -> &'static str {
    match whisper_language {
        "en" => "en-US",
        _ => "pt-BR",
    }
}

fn read_reply(stdout: &mut BufReader<ChildStdout>) -> Result<Reply, String> {
    let mut line = String::new();
    let n = stdout
        .read_line(&mut line)
        .map_err(|e| format!("Apple speech engine: {e}"))?;
    if n == 0 {
        return Err("Apple speech engine stopped".into());
    }
    serde_json::from_str(line.trim()).map_err(|e| format!("Apple speech engine: {e}"))
}

impl AppleSpeech {
    /// Start the helper and wait until its language model is installed and ready (the first
    /// time this downloads Apple's model for the language).
    pub fn spawn(helper: &Path, locale: &str) -> Result<Self, String> {
        let mut child = Command::new(helper)
            .arg("--locale")
            .arg(locale)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("Could not start Apple's speech engine: {e}"))?;
        let stdin = child.stdin.take().ok_or("no stdin")?;
        let mut stdout = BufReader::new(child.stdout.take().ok_or("no stdout")?);
        let reply = read_reply(&mut stdout)?;
        if let Some(e) = reply.error {
            let _ = child.kill();
            return Err(e);
        }
        if !reply.ready {
            let _ = child.kill();
            return Err("Apple speech engine did not start".into());
        }
        Ok(Self {
            child,
            stdin,
            stdout,
        })
    }

    pub fn factory(helper: PathBuf, locale: String) -> RecognizerFactory {
        Box::new(move || {
            AppleSpeech::spawn(&helper, &locale).map(|r| Box::new(r) as Box<dyn Recognizer>)
        })
    }

    /// Whether the helper can run here (macOS 26+, language supported, model installable).
    pub fn check(helper: &Path, locale: &str) -> Result<(), String> {
        let out = Command::new(helper)
            .args(["--check", "--locale", locale])
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("Could not start Apple's speech engine: {e}"))?;
        let line = String::from_utf8_lossy(&out.stdout);
        let reply: Reply = serde_json::from_str(line.lines().next().unwrap_or("").trim())
            .map_err(|_| "Apple speech engine did not answer".to_string())?;
        match (reply.ready, reply.error) {
            (true, _) => Ok(()),
            (_, Some(e)) => Err(e),
            _ => Err("Apple speech engine is not available".into()),
        }
    }
}

/// Request bytes: u32 sample count, then f32 samples, little-endian.
pub fn encode(audio: &[f32]) -> Vec<u8> {
    let mut buf = Vec::with_capacity(4 + audio.len() * 4);
    buf.extend_from_slice(&(audio.len() as u32).to_le_bytes());
    for s in audio {
        buf.extend_from_slice(&s.to_le_bytes());
    }
    buf
}

impl Recognizer for AppleSpeech {
    fn transcribe(&mut self, audio: &[f32], _language: &str) -> Result<String, String> {
        if audio.is_empty() {
            return Ok(String::new());
        }
        self.stdin
            .write_all(&encode(audio))
            .and_then(|_| self.stdin.flush())
            .map_err(|e| format!("Apple speech engine: {e}"))?;
        let reply = read_reply(&mut self.stdout)?;
        match (reply.text, reply.error) {
            (Some(t), _) => Ok(t),
            (_, Some(e)) => Err(e),
            _ => Ok(String::new()),
        }
    }
}

impl Drop for AppleSpeech {
    fn drop(&mut self) {
        let _ = self.stdin.write_all(&0u32.to_le_bytes());
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_count_then_samples() {
        let b = encode(&[0.5, -1.0]);
        assert_eq!(&b[..4], &2u32.to_le_bytes());
        assert_eq!(&b[4..8], &0.5f32.to_le_bytes());
        assert_eq!(&b[8..], &(-1.0f32).to_le_bytes());
        assert_eq!(locale_for("en"), "en-US");
        assert_eq!(locale_for("pt"), "pt-BR");
        assert_eq!(locale_for("auto"), "pt-BR");
    }

    #[cfg(unix)]
    #[test]
    fn talks_to_a_helper_over_pipes() {
        // A stand-in helper: ready, then answers every request with the same text.
        let dir = std::env::temp_dir().join(format!("mp-apple-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let helper = dir.join("fake-speech");
        std::fs::write(
            &helper,
            "#!/bin/sh\necho '{\"ready\":true,\"locale\":\"pt-BR\"}'\n\
             [ \"$1\" = \"--check\" ] && exit 0\n\
             while [ \"$(head -c 12 | wc -c)\" -eq 12 ]; do echo '{\"text\":\"olá mundo\"}'; done\n",
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&helper, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut r = AppleSpeech::spawn(&helper, "pt-BR").unwrap();
        assert_eq!(r.transcribe(&[0.1, 0.2], "pt").unwrap(), "olá mundo");
        assert!(AppleSpeech::check(&helper, "pt-BR").is_ok());
        let missing = dir.join("nope");
        assert!(AppleSpeech::spawn(&missing, "pt-BR").is_err());
    }
}
