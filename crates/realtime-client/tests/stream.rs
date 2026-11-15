//! Uplink behaviour against an in-process mock of the backend gateway.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use meetpilot_realtime::{
    spawn_stream, ApiError, LinkStatus, Source, StreamConfig, StreamEvent, TokenProvider,
};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;

#[derive(Clone, Copy)]
enum Behaviour {
    Normal,
    /// Close abruptly (1011) after this many frames on the first connection only.
    DropFirstAfter(usize),
    /// Close every connection right after auth with this code.
    Reject(u16),
}

#[derive(Default)]
struct Log {
    tokens: Vec<String>,
    frames_per_conn: Vec<usize>,
    seqs: Vec<u32>,
    controls: Vec<String>,
    bad_frames: usize,
}

async fn mock_gateway(behaviour: Behaviour) -> (String, Arc<Mutex<Log>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!(
        "ws://{}/api/v1/meetings/m1/stream",
        listener.local_addr().unwrap()
    );
    let log = Arc::new(Mutex::new(Log::default()));
    let l = Arc::clone(&log);
    let conn_no = Arc::new(AtomicUsize::new(0));
    tokio::spawn(async move {
        while let Ok((tcp, _)) = listener.accept().await {
            let l = Arc::clone(&l);
            let n = conn_no.fetch_add(1, Ordering::SeqCst);
            tokio::spawn(async move {
                let mut ws = tokio_tungstenite::accept_async(tcp).await.unwrap();
                let Some(Ok(Message::Text(auth))) = ws.next().await else {
                    return;
                };
                let v: serde_json::Value = serde_json::from_str(auth.as_str()).unwrap();
                l.lock()
                    .unwrap()
                    .tokens
                    .push(v["token"].as_str().unwrap().to_string());
                if let Behaviour::Reject(code) = behaviour {
                    let _ = ws
                        .close(Some(CloseFrame {
                            code: CloseCode::from(code),
                            reason: "no".into(),
                        }))
                        .await;
                    return;
                }
                l.lock().unwrap().frames_per_conn.push(0);
                let env = |t: &str, seq: u64, payload: serde_json::Value| {
                    Message::text(
                        serde_json::json!({"v":1,"type":t,"meeting_id":"m1","seq":seq,"ts":"2026-11-15T00:00:00Z","payload":payload})
                            .to_string(),
                    )
                };
                ws.send(env("capture.started", 0, serde_json::json!({})))
                    .await
                    .unwrap();
                let mut seq = 1;
                let mut frames = 0usize;
                while let Some(Ok(msg)) = ws.next().await {
                    match msg {
                        Message::Binary(b) => {
                            if b.len() != 645 {
                                l.lock().unwrap().bad_frames += 1;
                                continue;
                            }
                            frames += 1;
                            {
                                let mut g = l.lock().unwrap();
                                *g.frames_per_conn.last_mut().unwrap() += 1;
                                g.seqs.push(u32::from_le_bytes([b[1], b[2], b[3], b[4]]));
                            }
                            if let Behaviour::DropFirstAfter(k) = behaviour {
                                if n == 0 && frames == k {
                                    let _ = ws
                                        .close(Some(CloseFrame {
                                            code: CloseCode::Error,
                                            reason: "boom".into(),
                                        }))
                                        .await;
                                    return;
                                }
                            }
                            if frames % 50 == 0 {
                                ws.send(env(
                                    "transcript.partial",
                                    seq,
                                    serde_json::json!({"text":"..."}),
                                ))
                                .await
                                .unwrap();
                                seq += 1;
                            }
                        }
                        Message::Text(t) => {
                            let v: serde_json::Value = serde_json::from_str(t.as_str()).unwrap();
                            let kind = v["type"].as_str().unwrap().to_string();
                            l.lock().unwrap().controls.push(kind.clone());
                            if kind == "capture.stopped" {
                                ws.send(env(
                                    "transcript.final",
                                    seq,
                                    serde_json::json!({"text":"fim"}),
                                ))
                                .await
                                .unwrap();
                                ws.send(env("capture.stopped", seq + 1, serde_json::json!({})))
                                    .await
                                    .unwrap();
                                let _ = ws.close(None).await;
                                return;
                            }
                        }
                        _ => {}
                    }
                }
            });
        }
    });
    (url, log)
}

struct Collected {
    statuses: Mutex<Vec<LinkStatus>>,
    server: Mutex<Vec<serde_json::Value>>,
}

fn collector() -> (Arc<Collected>, meetpilot_realtime::EventSink) {
    let c = Arc::new(Collected {
        statuses: Mutex::new(vec![]),
        server: Mutex::new(vec![]),
    });
    let c2 = Arc::clone(&c);
    let sink: meetpilot_realtime::EventSink = Arc::new(move |e| match e {
        StreamEvent::Status(s) => c2.statuses.lock().unwrap().push(s),
        StreamEvent::Server(v) => c2.server.lock().unwrap().push(v),
    });
    (c, sink)
}

fn counting_tokens() -> (TokenProvider, Arc<AtomicUsize>) {
    let n = Arc::new(AtomicUsize::new(0));
    let n2 = Arc::clone(&n);
    let p: TokenProvider = Arc::new(move || {
        let i = n2.fetch_add(1, Ordering::SeqCst);
        Box::pin(async move { Ok::<_, ApiError>(format!("refreshed-{i}")) })
    });
    (p, n)
}

fn fast(url: String) -> StreamConfig {
    StreamConfig {
        base_backoff: Duration::from_millis(20),
        max_backoff: Duration::from_millis(100),
        connect_timeout: Duration::from_secs(2),
        stop_timeout: Duration::from_secs(2),
        ..StreamConfig::new(url)
    }
}

async fn wait_for(mut f: impl FnMut() -> bool) -> bool {
    for _ in 0..300 {
        if f() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    f()
}

const PCM: [i16; 320] = [7; 320];

#[tokio::test]
async fn authenticates_streams_frames_and_flushes_on_stop() {
    let (url, log) = mock_gateway(Behaviour::Normal).await;
    let (c, sink) = collector();
    let (tokens, refreshed) = counting_tokens();
    let h = spawn_stream(fast(url), "initial-token".into(), tokens, sink);
    for i in 0..120 {
        assert!(h.send_frame(Source::System, i, &PCM));
    }
    assert!(wait_for(|| log.lock().unwrap().seqs.len() == 120).await);
    h.pause();
    h.resume();
    h.stop().await;
    assert!(h.is_finished() || wait_for(|| h.is_finished()).await);

    let g = log.lock().unwrap();
    assert_eq!(
        g.tokens,
        vec!["initial-token"],
        "first connect uses the initial token"
    );
    assert_eq!(refreshed.load(Ordering::SeqCst), 0);
    assert_eq!(g.seqs, (0..120).collect::<Vec<_>>(), "in order, none lost");
    assert_eq!(g.bad_frames, 0);
    assert_eq!(
        g.controls,
        vec!["capture.paused", "capture.resumed", "capture.stopped"]
    );

    let types: Vec<String> = c
        .server
        .lock()
        .unwrap()
        .iter()
        .map(|v| v["type"].as_str().unwrap().into())
        .collect();
    assert_eq!(types.first().unwrap(), "capture.started");
    assert!(types.contains(&"transcript.partial".to_string()));
    assert!(
        types.ends_with(&[
            "transcript.final".to_string(),
            "capture.stopped".to_string()
        ]),
        "final transcript arrives before stop() returns: {types:?}"
    );
    let st = c.statuses.lock().unwrap();
    assert_eq!(st.first(), Some(&LinkStatus::Connecting));
    assert!(st.contains(&LinkStatus::Connected));
    assert_eq!(st.last(), Some(&LinkStatus::Closed));
}

#[tokio::test]
async fn reconnects_with_fresh_token_and_delivers_buffered_frames() {
    let (url, log) = mock_gateway(Behaviour::DropFirstAfter(30)).await;
    let (c, sink) = collector();
    let (tokens, refreshed) = counting_tokens();
    let h = spawn_stream(fast(url), "initial".into(), tokens, sink);
    for i in 0..200u32 {
        assert!(
            h.send_frame(Source::Microphone, i, &PCM),
            "buffer absorbs the outage"
        );
        if i % 20 == 0 {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }
    // Frames are sent once each, except possibly the in-flight one when the link dropped.
    assert!(
        wait_for(|| {
            let g = log.lock().unwrap();
            let mut s = g.seqs.clone();
            s.sort_unstable();
            s.dedup();
            s.len() == 200
        })
        .await
    );
    h.stop().await;

    let g = log.lock().unwrap();
    assert!(
        g.frames_per_conn.len() >= 2,
        "reconnected: {:?}",
        g.frames_per_conn
    );
    assert_eq!(g.tokens[0], "initial");
    assert!(
        g.tokens[1].starts_with("refreshed-"),
        "fresh token for reconnect"
    );
    assert!(refreshed.load(Ordering::SeqCst) >= 1);
    assert_eq!(h.dropped_frames(), 0);
    let st = c.statuses.lock().unwrap();
    assert!(st
        .iter()
        .any(|s| matches!(s, LinkStatus::Reconnecting { attempt: 1, .. })));
    assert_eq!(st.last(), Some(&LinkStatus::Closed));
}

#[tokio::test]
async fn quota_close_is_fatal_and_not_retried() {
    let (url, log) = mock_gateway(Behaviour::Reject(4402)).await;
    let (c, sink) = collector();
    let (tokens, refreshed) = counting_tokens();
    let h = spawn_stream(fast(url), "t".into(), tokens, sink);
    assert!(wait_for(|| h.is_finished()).await);
    assert_eq!(
        log.lock().unwrap().tokens.len(),
        1,
        "no retry after a fatal close"
    );
    assert_eq!(refreshed.load(Ordering::SeqCst), 0);
    match c.statuses.lock().unwrap().last() {
        Some(LinkStatus::Failed { code, reason }) => {
            assert_eq!(*code, Some(4402));
            assert!(reason.contains("minutes"));
        }
        other => panic!("expected Failed, got {other:?}"),
    };
}

#[tokio::test]
async fn token_refresh_failure_that_is_not_transient_is_fatal() {
    let (url, _log) = mock_gateway(Behaviour::DropFirstAfter(1)).await;
    let (c, sink) = collector();
    let tokens: TokenProvider = Arc::new(|| Box::pin(async { Err(ApiError::Unauthorized) }));
    let h = spawn_stream(fast(url), "t".into(), tokens, sink);
    h.send_frame(Source::System, 0, &PCM);
    assert!(wait_for(|| h.is_finished()).await);
    assert!(matches!(
        c.statuses.lock().unwrap().last(),
        Some(LinkStatus::Failed { .. })
    ));
}

#[tokio::test]
async fn overflow_while_server_unreachable_is_counted_never_blocking() {
    // Nothing listens on this port.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/x", listener.local_addr().unwrap());
    drop(listener);
    let (_c, sink) = collector();
    let (tokens, _) = counting_tokens();
    let cfg = StreamConfig {
        queue_frames: 10,
        ..fast(url)
    };
    let h = spawn_stream(cfg, "t".into(), tokens, sink);
    let started = std::time::Instant::now();
    let accepted = (0..50)
        .filter(|i| h.send_frame(Source::System, *i, &PCM))
        .count();
    assert!(
        started.elapsed() < Duration::from_millis(50),
        "send_frame must not block"
    );
    assert_eq!(accepted, 10);
    assert_eq!(h.dropped_frames(), 40);
    h.stop().await;
}

#[tokio::test]
async fn invalid_frames_are_rejected_locally() {
    let (url, log) = mock_gateway(Behaviour::Normal).await;
    let (_c, sink) = collector();
    let (tokens, _) = counting_tokens();
    let h = spawn_stream(fast(url), "t".into(), tokens, sink);
    assert!(!h.send_frame(Source::System, 0, &[0; 10]));
    h.stop().await;
    assert_eq!(log.lock().unwrap().bad_frames, 0);
}
