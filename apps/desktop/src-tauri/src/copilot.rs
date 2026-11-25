//! Ask-Copilot. The question and the recent transcript (which the UI already shows) go to
//! the MeetPilot server, which calls the AI provider; nothing is stored. Answers are
//! broadcast as `copilot://answer` so the main window and the overlay stay in sync.

use std::sync::atomic::{AtomicU64, Ordering};

use meetpilot_realtime::{ContextSegment, CopilotAnswer, CopilotRequest};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, State};

use crate::account::AccountState;
use crate::capture::{CaptureState, CommandError};

pub const EVT_ANSWER: &str = "copilot://answer";
pub const EVT_PENDING: &str = "copilot://pending";
const MAX_SEGMENTS: usize = 400;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AskRequest {
    pub mode: String,
    #[serde(default)]
    pub question: Option<String>,
    #[serde(default)]
    pub transcript: Vec<ContextSegment>,
    #[serde(default = "default_tone")]
    pub tone: String,
    #[serde(default)]
    pub language: Option<String>,
    #[serde(default)]
    pub user_context: Option<String>,
    #[serde(default)]
    pub translate_to: Option<String>,
    #[serde(default)]
    pub scenario: Option<String>,
}

fn default_tone() -> String {
    "natural".into()
}

#[derive(Debug, Clone, Serialize)]
pub struct AnswerEvent {
    pub id: u64,
    pub mode: String,
    pub question: Option<String>,
    #[serde(flatten)]
    pub answer: CopilotAnswer,
}

#[derive(Debug, Clone, Serialize)]
pub struct PendingEvent {
    pub id: u64,
    pub mode: String,
    pub question: Option<String>,
    /// "pending" | "failed"
    pub state: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Keep only the newest segments; the server applies its own budget too.
pub fn trim_transcript(mut segments: Vec<ContextSegment>) -> Vec<ContextSegment> {
    segments.retain(|s| !s.text.trim().is_empty());
    if segments.len() > MAX_SEGMENTS {
        segments.drain(..segments.len() - MAX_SEGMENTS);
    }
    for s in &mut segments {
        if s.text.chars().count() > 2_000 {
            s.text = s.text.chars().take(2_000).collect();
        }
    }
    segments
}

#[tauri::command]
pub async fn ask_copilot(
    app: AppHandle,
    capture: State<'_, CaptureState>,
    accounts: State<'_, AccountState>,
    request: AskRequest,
) -> Result<AnswerEvent, CommandError> {
    let meeting_id = capture.meeting_for_copilot().ok_or_else(|| {
        CommandError::new(
            "no_meeting",
            "Start a capture with live transcription to use the copilot.",
        )
    })?;
    let (client, access) = accounts.access().await?;
    let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    let pending = |state: &'static str, error: Option<String>| PendingEvent {
        id,
        mode: request.mode.clone(),
        question: request.question.clone(),
        state,
        error,
    };
    let _ = app.emit(EVT_PENDING, pending("pending", None));
    let body = CopilotRequest {
        mode: request.mode.clone(),
        question: request.question.clone(),
        transcript: trim_transcript(request.transcript.clone()),
        tone: request.tone.clone(),
        language: request.language.clone(),
        user_context: crate::capture::clean_user_context(request.user_context.as_deref()),
        translate_to: request
            .translate_to
            .clone()
            .filter(|l| l == "pt-BR" || l == "en"),
        scenario: request
            .scenario
            .clone()
            .filter(|s| s == "interview" || s == "meeting"),
    };
    match client.copilot(&access, &meeting_id, &body).await {
        Ok(answer) => {
            let ev = AnswerEvent {
                id,
                mode: request.mode,
                question: request.question,
                answer,
            };
            let _ = app.emit(EVT_ANSWER, ev.clone());
            Ok(ev)
        }
        Err(e) => {
            let _ = app.emit(EVT_PENDING, pending("failed", Some(e.to_string())));
            Err(e.into())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seg(i: usize, text: &str) -> ContextSegment {
        ContextSegment {
            source: "system".into(),
            text: text.into(),
            start_ms: i as u64 * 1000,
        }
    }

    #[test]
    fn keeps_newest_segments_and_drops_empty_ones() {
        let segs: Vec<_> = (0..450)
            .map(|i| seg(i, if i % 50 == 0 { "  " } else { "fala" }))
            .collect();
        let out = trim_transcript(segs);
        assert_eq!(out.len(), MAX_SEGMENTS);
        assert_eq!(out.last().unwrap().start_ms, 449_000);
        assert!(out.iter().all(|s| !s.text.trim().is_empty()));
    }

    #[test]
    fn caps_very_long_segments() {
        let out = trim_transcript(vec![seg(0, &"é".repeat(5_000))]);
        assert_eq!(out[0].text.chars().count(), 2_000);
    }

    #[test]
    fn ask_request_defaults() {
        let with_ctx: AskRequest =
            serde_json::from_str(r#"{"mode":"reply","userContext":"Sou o Rafael"}"#).unwrap();
        assert_eq!(with_ctx.user_context.as_deref(), Some("Sou o Rafael"));
        let r: AskRequest = serde_json::from_str(r#"{"mode":"reply"}"#).unwrap();
        assert_eq!(r.tone, "natural");
        assert!(r.transcript.is_empty() && r.question.is_none());
    }
}
