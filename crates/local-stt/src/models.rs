//! Whisper model catalog and verified downloads.
//!
//! Models are the official whisper.cpp GGML files on Hugging Face. Each download is
//! streamed to `<file>.part`, hashed while writing, compared with the SHA-1 published in
//! whisper.cpp's `models/README.md`, and only then renamed into place. A `<file>.verified`
//! marker records the checksum so a later truncated/corrupted file is not trusted.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use futures_util::StreamExt;
use serde::Serialize;
use sha1::{Digest, Sha1};
use thiserror::Error;
use tokio::io::AsyncWriteExt;

pub const DEFAULT_BASE_URL: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct ModelSpec {
    pub id: &'static str,
    pub file: &'static str,
    /// Approximate size for the UI (MiB from the whisper.cpp table).
    pub approx_bytes: u64,
    /// SHA-1 from whisper.cpp `models/README.md`.
    pub sha1: &'static str,
    /// Offered in the app (tiny is only used by automated tests).
    pub listed: bool,
}

const MIB: u64 = 1024 * 1024;

pub const MODELS: &[ModelSpec] = &[
    ModelSpec {
        id: "base",
        file: "ggml-base.bin",
        approx_bytes: 142 * MIB,
        sha1: "465707469ff3a37a2b9b8d8f89f2f99de7299dac",
        listed: true,
    },
    ModelSpec {
        id: "small",
        file: "ggml-small.bin",
        approx_bytes: 466 * MIB,
        sha1: "55356645c2b361a969dfd0ef2c5a50d530afd8d5",
        listed: true,
    },
    ModelSpec {
        id: "large-v3-turbo-q5_0",
        file: "ggml-large-v3-turbo-q5_0.bin",
        approx_bytes: 547 * MIB,
        sha1: "e050f7970618a659205450ad97eb95a18d69c9ee",
        listed: true,
    },
    ModelSpec {
        id: "tiny",
        file: "ggml-tiny.bin",
        approx_bytes: 75 * MIB,
        sha1: "bd577a113a864445d4c299885e0cb97d4ba92b5f",
        listed: false,
    },
];

pub fn spec(id: &str) -> Option<&'static ModelSpec> {
    MODELS.iter().find(|m| m.id == id)
}

/// Default recommendation: Apple Silicon runs the best model on the GPU; elsewhere (CPU
/// only) the balanced model keeps up in real time on typical laptops.
pub fn recommended_model() -> &'static str {
    if cfg!(target_os = "macos") {
        "large-v3-turbo-q5_0"
    } else {
        "small"
    }
}

#[derive(Debug, Error, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ModelError {
    #[error("Unknown model {id}.")]
    Unknown { id: String },
    #[error("Download failed: {detail}")]
    Network { detail: String },
    #[error("The downloaded file is corrupted (checksum mismatch). Please try again.")]
    Checksum,
    #[error("Download cancelled.")]
    Cancelled,
    #[error("Could not save the model: {detail}")]
    Io { detail: String },
}

fn io(e: std::io::Error) -> ModelError {
    ModelError::Io {
        detail: e.to_string(),
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ModelInfo {
    pub id: String,
    pub approx_bytes: u64,
    pub downloaded: bool,
    pub recommended: bool,
}

#[derive(Debug, Clone)]
pub struct ModelStore {
    dir: PathBuf,
    base_url: String,
}

impl ModelStore {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self::with_base_url(dir, DEFAULT_BASE_URL)
    }

    pub fn with_base_url(dir: impl Into<PathBuf>, base_url: &str) -> Self {
        let mut base = base_url.to_string();
        if !base.ends_with('/') {
            base.push('/');
        }
        Self {
            dir: dir.into(),
            base_url: base,
        }
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn path_of(&self, spec: &ModelSpec) -> PathBuf {
        self.dir.join(spec.file)
    }

    fn marker_of(&self, spec: &ModelSpec) -> PathBuf {
        self.dir.join(format!("{}.verified", spec.file))
    }

    /// True only if the file exists and its recorded checksum matches the catalog.
    pub fn is_ready(&self, spec: &ModelSpec) -> bool {
        self.path_of(spec).is_file()
            && std::fs::read_to_string(self.marker_of(spec))
                .map(|s| s.trim() == spec.sha1)
                .unwrap_or(false)
    }

    pub fn list(&self) -> Vec<ModelInfo> {
        MODELS
            .iter()
            .filter(|m| m.listed)
            .map(|m| ModelInfo {
                id: m.id.into(),
                approx_bytes: m.approx_bytes,
                downloaded: self.is_ready(m),
                recommended: m.id == recommended_model(),
            })
            .collect()
    }

    pub fn delete(&self, spec: &ModelSpec) -> Result<(), ModelError> {
        for p in [
            self.path_of(spec),
            self.marker_of(spec),
            self.dir.join(format!("{}.part", spec.file)),
        ] {
            match std::fs::remove_file(&p) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(io(e)),
            }
        }
        Ok(())
    }

    /// Download and verify. `progress(downloaded, total)` is called as data arrives.
    pub async fn download(
        &self,
        spec: &ModelSpec,
        progress: impl Fn(u64, Option<u64>),
        cancel: Arc<AtomicBool>,
    ) -> Result<PathBuf, ModelError> {
        tokio::fs::create_dir_all(&self.dir).await.map_err(io)?;
        let url = format!("{}{}", self.base_url, spec.file);
        let resp = reqwest::Client::builder()
            .user_agent(concat!("MeetPilot-Desktop/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|e| ModelError::Network {
                detail: e.to_string(),
            })?
            .get(&url)
            .send()
            .await
            .map_err(|e| ModelError::Network {
                detail: e.without_url().to_string(),
            })?;
        if !resp.status().is_success() {
            return Err(ModelError::Network {
                detail: format!("HTTP {}", resp.status().as_u16()),
            });
        }
        let total = resp.content_length();
        let part = self.dir.join(format!("{}.part", spec.file));
        let mut file = tokio::fs::File::create(&part).await.map_err(io)?;
        let mut hasher = Sha1::new();
        let mut done: u64 = 0;
        let mut stream = resp.bytes_stream();
        let result: Result<(), ModelError> = async {
            while let Some(chunk) = stream.next().await {
                if cancel.load(Ordering::Relaxed) {
                    return Err(ModelError::Cancelled);
                }
                let chunk = chunk.map_err(|e| ModelError::Network {
                    detail: e.without_url().to_string(),
                })?;
                hasher.update(&chunk);
                file.write_all(&chunk).await.map_err(io)?;
                done += chunk.len() as u64;
                progress(done, total);
            }
            file.flush().await.map_err(io)?;
            Ok(())
        }
        .await;
        drop(file);
        if let Err(e) = result {
            let _ = tokio::fs::remove_file(&part).await;
            return Err(e);
        }
        let digest: String = hasher
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        if digest != spec.sha1 {
            let _ = tokio::fs::remove_file(&part).await;
            return Err(ModelError::Checksum);
        }
        let dest = self.path_of(spec);
        tokio::fs::rename(&part, &dest).await.map_err(io)?;
        tokio::fs::write(self.marker_of(spec), spec.sha1)
            .await
            .map_err(io)?;
        Ok(dest)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_is_consistent() {
        for m in MODELS {
            assert_eq!(m.sha1.len(), 40, "{}", m.id);
            assert!(m.sha1.chars().all(|c| c.is_ascii_hexdigit()));
            assert!(m.file.starts_with("ggml-") && m.file.ends_with(".bin"));
        }
        assert!(spec(recommended_model()).unwrap().listed);
        assert!(!spec("tiny").unwrap().listed, "tiny is for tests only");
    }

    #[test]
    fn listing_hides_internal_models_and_marks_recommendation() {
        let dir = std::env::temp_dir().join(format!("mp-models-{}", std::process::id()));
        let store = ModelStore::new(&dir);
        let list = store.list();
        assert_eq!(list.len(), 3);
        assert!(list.iter().all(|m| !m.downloaded));
        assert_eq!(list.iter().filter(|m| m.recommended).count(), 1);
    }
}
