//! Verified model download against a local HTTP server.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use meetpilot_local_stt::{ModelError, ModelSpec, ModelStore};
use sha1::{Digest, Sha1};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

async fn serve(body: Vec<u8>) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        while let Ok((mut sock, _)) = listener.accept().await {
            let body = body.clone();
            tokio::spawn(async move {
                let mut buf = [0u8; 2048];
                let _ = sock.read(&mut buf).await;
                let head = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nContent-Type: application/octet-stream\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = sock.write_all(head.as_bytes()).await;
                for chunk in body.chunks(64 * 1024) {
                    if sock.write_all(chunk).await.is_err() {
                        return;
                    }
                }
            });
        }
    });
    format!("http://{addr}/")
}

fn hex_sha1(data: &[u8]) -> String {
    Sha1::digest(data)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn leak(s: String) -> &'static str {
    Box::leak(s.into_boxed_str())
}

fn tmpdir(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("mp-dl-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    d
}

#[tokio::test]
async fn downloads_verifies_and_marks_ready() {
    let body: Vec<u8> = (0..1_000_000u32).map(|i| (i % 251) as u8).collect();
    let base = serve(body.clone()).await;
    let spec = ModelSpec {
        id: "test",
        file: "ggml-test.bin",
        approx_bytes: body.len() as u64,
        sha1: leak(hex_sha1(&body)),
        listed: false,
    };
    let store = ModelStore::with_base_url(tmpdir("ok"), &base);
    let seen = Arc::new(AtomicU64::new(0));
    let s2 = Arc::clone(&seen);
    let path = store
        .download(
            &spec,
            move |done, total| {
                assert_eq!(total, Some(1_000_000));
                s2.store(done, Ordering::SeqCst);
            },
            Arc::new(AtomicBool::new(false)),
        )
        .await
        .unwrap();
    assert_eq!(std::fs::read(&path).unwrap(), body);
    assert_eq!(
        seen.load(Ordering::SeqCst),
        1_000_000,
        "progress reported to the end"
    );
    assert!(store.is_ready(&spec));
    assert!(!store.dir().join("ggml-test.bin.part").exists());

    store.delete(&spec).unwrap();
    assert!(!store.is_ready(&spec));
    assert!(!path.exists());
}

#[tokio::test]
async fn corrupted_download_is_rejected_and_removed() {
    let base = serve(vec![7u8; 50_000]).await;
    let spec = ModelSpec {
        id: "test",
        file: "ggml-bad.bin",
        approx_bytes: 50_000,
        sha1: "0000000000000000000000000000000000000000",
        listed: false,
    };
    let store = ModelStore::with_base_url(tmpdir("bad"), &base);
    let err = store
        .download(&spec, |_, _| {}, Arc::new(AtomicBool::new(false)))
        .await
        .unwrap_err();
    assert_eq!(err, ModelError::Checksum);
    assert!(!store.is_ready(&spec));
    assert!(!store.path_of(&spec).exists());
    assert!(!store.dir().join("ggml-bad.bin.part").exists());
}

#[tokio::test]
async fn cancel_stops_and_cleans_up() {
    let body = vec![1u8; 5_000_000];
    let base = serve(body.clone()).await;
    let spec = ModelSpec {
        id: "test",
        file: "ggml-cancel.bin",
        approx_bytes: 5_000_000,
        sha1: leak(hex_sha1(&body)),
        listed: false,
    };
    let store = ModelStore::with_base_url(tmpdir("cancel"), &base);
    let cancel = Arc::new(AtomicBool::new(false));
    let c2 = Arc::clone(&cancel);
    let err = store
        .download(
            &spec,
            move |done, _| {
                if done > 100_000 {
                    c2.store(true, Ordering::SeqCst);
                }
            },
            cancel,
        )
        .await
        .unwrap_err();
    assert_eq!(err, ModelError::Cancelled);
    assert!(!store.is_ready(&spec));
    assert!(!store.dir().join("ggml-cancel.bin.part").exists());
}

#[tokio::test]
async fn tampered_file_after_download_is_not_trusted() {
    let body = vec![3u8; 10_000];
    let base = serve(body.clone()).await;
    let spec = ModelSpec {
        id: "test",
        file: "ggml-tamper.bin",
        approx_bytes: 10_000,
        sha1: leak(hex_sha1(&body)),
        listed: false,
    };
    let store = ModelStore::with_base_url(tmpdir("tamper"), &base);
    store
        .download(&spec, |_, _| {}, Arc::new(AtomicBool::new(false)))
        .await
        .unwrap();
    assert!(store.is_ready(&spec));
    // A marker for a different checksum (e.g. catalog updated) invalidates the file.
    std::fs::write(store.dir().join("ggml-tamper.bin.verified"), "deadbeef").unwrap();
    assert!(!store.is_ready(&spec));
}

#[tokio::test]
async fn http_errors_are_reported() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        while let Ok((mut s, _)) = listener.accept().await {
            let mut b = [0u8; 1024];
            let _ = s.read(&mut b).await;
            let _ = s
                .write_all(
                    b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                )
                .await;
        }
    });
    let spec = ModelSpec {
        id: "x",
        file: "ggml-missing.bin",
        approx_bytes: 1,
        sha1: "0000000000000000000000000000000000000000",
        listed: false,
    };
    let store = ModelStore::with_base_url(tmpdir("404"), &format!("http://{addr}/"));
    let err = store
        .download(&spec, |_, _| {}, Arc::new(AtomicBool::new(false)))
        .await
        .unwrap_err();
    assert_eq!(
        err,
        ModelError::Network {
            detail: "HTTP 404".into()
        }
    );
}
