//! REST client for the MeetPilot backend (`/api/v1`). Provider API keys never live here.

use serde::{Deserialize, Serialize};
use thiserror::Error;
use url::Url;

#[derive(Debug, Clone, Error, Serialize, PartialEq, Eq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ApiError {
    #[error("Invalid server address: {detail}")]
    InvalidUrl { detail: String },
    #[error("Not signed in, or the session expired.")]
    Unauthorized,
    #[error("Your monthly transcription minutes are used up.")]
    QuotaExceeded,
    #[error("Not found.")]
    NotFound,
    #[error("{message}")]
    Conflict { message: String },
    #[error("{message}")]
    Rejected { status: u16, message: String },
    #[error("Too many attempts. Try again in a few minutes.")]
    RateLimited,
    #[error("{message}")]
    Unavailable { status: u16, message: String },
    #[error("Cannot reach the MeetPilot server: {detail}")]
    Network { detail: String },
    #[error("Unexpected server response ({status}).")]
    Server { status: u16 },
}

impl ApiError {
    /// Worth retrying later (network blips, server errors).
    pub fn is_transient(&self) -> bool {
        matches!(
            self,
            ApiError::Network { .. }
                | ApiError::Server { .. }
                | ApiError::RateLimited
                | ApiError::Unavailable { status: 503, .. }
        )
    }
}

/// Map an HTTP status and FastAPI `{"detail": ...}` body to an error.
pub fn error_for_status(status: u16, body: &str) -> ApiError {
    let message = serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| match v.get("detail") {
            Some(serde_json::Value::String(s)) => Some(s.clone()),
            Some(other) => Some(other.to_string()),
            None => None,
        });
    let has_detail = message.is_some();
    let message = message.unwrap_or_else(|| format!("HTTP {status}"));
    match status {
        401 => ApiError::Unauthorized,
        402 => ApiError::QuotaExceeded,
        404 => ApiError::NotFound,
        409 => ApiError::Conflict { message },
        429 => ApiError::RateLimited,
        400..=499 => ApiError::Rejected { status, message },
        502 | 503 if has_detail => ApiError::Unavailable { status, message },
        _ => ApiError::Server { status },
    }
}

/// Validate the server address. HTTPS is required except for local development hosts.
pub fn parse_base_url(raw: &str) -> Result<Url, ApiError> {
    let mut url = Url::parse(raw.trim()).map_err(|e| ApiError::InvalidUrl {
        detail: e.to_string(),
    })?;
    let local = matches!(
        url.host_str(),
        Some("localhost") | Some("127.0.0.1") | Some("[::1]") | Some("::1")
    );
    match url.scheme() {
        "https" => {}
        "http" if local => {}
        "http" => {
            return Err(ApiError::InvalidUrl {
                detail: "use https:// (plain http is only allowed for localhost)".into(),
            })
        }
        other => {
            return Err(ApiError::InvalidUrl {
                detail: format!("unsupported scheme {other}"),
            })
        }
    }
    if !url.path().ends_with('/') {
        let p = format!("{}/", url.path());
        url.set_path(&p);
    }
    Ok(url)
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct TokenPair {
    pub access_token: String,
    pub expires_in: u64,
    pub refresh_token: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct Me {
    pub id: String,
    pub email: String,
    pub plan: String,
    pub store_transcripts: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct Usage {
    pub used_seconds: f64,
    pub limit_seconds: f64,
    pub remaining_seconds: f64,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct StreamGrant {
    pub stream_token: String,
    pub expires_in: u64,
    pub stream_path: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct MeetingOut {
    pub id: String,
    pub language: String,
    pub status: String,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct MeetingCreated {
    pub meeting: MeetingOut,
    pub stream: StreamGrant,
}

/// One transcript line sent as copilot context (the server never stores it).
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct ContextSegment {
    pub source: String,
    pub text: String,
    pub start_ms: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct CopilotRequest {
    /// "ask" | "reply" | "summarize"
    pub mode: String,
    pub question: Option<String>,
    pub transcript: Vec<ContextSegment>,
    /// "natural" | "concise" | "technical" | "formal"
    pub tone: String,
    pub language: Option<String>,
    /// "My context" written by the user; omitted when empty.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_context: Option<String>,
    /// Also translate the answer into this language ("pt-BR" | "en"); omitted when none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub translate_to: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct CopilotAnswer {
    pub answer: String,
    /// "from_conversation" | "from_user_context" | "general_suggestion" | "needs_confirmation"
    pub grounding: String,
    pub explanation: String,
    pub missing_info: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub answer_translation: Option<String>,
    pub provider: String,
    pub requests_remaining: i64,
}

#[derive(Clone)]
pub struct ApiClient {
    base: Url,
    http: reqwest::Client,
}

impl ApiClient {
    pub fn new(base_url: &str) -> Result<Self, ApiError> {
        let base = parse_base_url(base_url)?;
        let http = reqwest::Client::builder()
            .user_agent(concat!("MeetPilot-Desktop/", env!("CARGO_PKG_VERSION")))
            .timeout(std::time::Duration::from_secs(20))
            .build()
            .map_err(|e| ApiError::Network {
                detail: e.to_string(),
            })?;
        Ok(Self { base, http })
    }

    pub fn base_url(&self) -> &Url {
        &self.base
    }

    fn url(&self, path: &str) -> Url {
        self.base
            .join(path.trim_start_matches('/'))
            .expect("static API paths are valid")
    }

    /// `ws(s)://host/<stream_path>` for a path returned by the API.
    pub fn ws_url(&self, stream_path: &str) -> String {
        let mut u = self.url(stream_path);
        let scheme = if u.scheme() == "https" { "wss" } else { "ws" };
        u.set_scheme(scheme).expect("ws/wss are valid schemes");
        u.to_string()
    }

    async fn send<T: for<'de> Deserialize<'de>>(
        &self,
        req: reqwest::RequestBuilder,
    ) -> Result<T, ApiError> {
        let resp = req.send().await.map_err(|e| ApiError::Network {
            detail: e.without_url().to_string(),
        })?;
        let status = resp.status().as_u16();
        let body = resp.text().await.map_err(|e| ApiError::Network {
            detail: e.without_url().to_string(),
        })?;
        if !(200..300).contains(&status) {
            return Err(error_for_status(status, &body));
        }
        let body = if body.is_empty() { "null" } else { &body };
        serde_json::from_str(body).map_err(|_| ApiError::Server { status })
    }

    pub async fn register(&self, email: &str, password: &str) -> Result<Me, ApiError> {
        let req = self
            .http
            .post(self.url("api/v1/auth/register"))
            .json(&serde_json::json!({ "email": email, "password": password }));
        self.send(req).await
    }

    pub async fn login(&self, email: &str, password: &str) -> Result<TokenPair, ApiError> {
        let req = self
            .http
            .post(self.url("api/v1/auth/login"))
            .json(&serde_json::json!({ "email": email, "password": password }));
        self.send(req).await
    }

    pub async fn refresh(&self, refresh_token: &str) -> Result<TokenPair, ApiError> {
        let req = self
            .http
            .post(self.url("api/v1/auth/refresh"))
            .json(&serde_json::json!({ "refresh_token": refresh_token }));
        self.send(req).await
    }

    pub async fn logout(&self, refresh_token: &str) -> Result<(), ApiError> {
        let req = self
            .http
            .post(self.url("api/v1/auth/logout"))
            .json(&serde_json::json!({ "refresh_token": refresh_token }));
        self.send::<serde_json::Value>(req).await.map(|_| ())
    }

    pub async fn me(&self, access: &str) -> Result<Me, ApiError> {
        self.send(self.http.get(self.url("api/v1/me")).bearer_auth(access))
            .await
    }

    pub async fn usage(&self, access: &str) -> Result<Usage, ApiError> {
        self.send(self.http.get(self.url("api/v1/usage")).bearer_auth(access))
            .await
    }

    pub async fn create_meeting(
        &self,
        access: &str,
        language: &str,
        local: bool,
    ) -> Result<MeetingCreated, ApiError> {
        let stt = if local { "local" } else { "cloud" };
        let req = self
            .http
            .post(self.url("api/v1/meetings"))
            .bearer_auth(access)
            .json(&serde_json::json!({ "language": language, "stt": stt }));
        self.send(req).await
    }

    pub async fn stream_token(
        &self,
        access: &str,
        meeting_id: &str,
    ) -> Result<StreamGrant, ApiError> {
        let req = self
            .http
            .post(self.url(&format!("api/v1/meetings/{meeting_id}/stream-token")))
            .bearer_auth(access);
        self.send(req).await
    }

    pub async fn copilot(
        &self,
        access: &str,
        meeting_id: &str,
        request: &CopilotRequest,
    ) -> Result<CopilotAnswer, ApiError> {
        let req = self
            .http
            .post(self.url(&format!("api/v1/meetings/{meeting_id}/copilot")))
            .bearer_auth(access)
            .timeout(std::time::Duration::from_secs(45))
            .json(request);
        self.send(req).await
    }

    pub async fn finish_meeting(&self, access: &str, meeting_id: &str) -> Result<(), ApiError> {
        let req = self
            .http
            .post(self.url(&format!("api/v1/meetings/{meeting_id}/finish")))
            .bearer_auth(access);
        self.send::<serde_json::Value>(req).await.map(|_| ())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn https_required_except_localhost() {
        assert!(parse_base_url("https://api.meetpilot.app").is_ok());
        assert!(parse_base_url("http://localhost:8000").is_ok());
        assert!(parse_base_url("http://127.0.0.1:8000").is_ok());
        assert!(matches!(
            parse_base_url("http://api.meetpilot.app"),
            Err(ApiError::InvalidUrl { .. })
        ));
        assert!(parse_base_url("ftp://x").is_err());
        assert!(parse_base_url("not a url").is_err());
    }

    #[test]
    fn ws_url_follows_scheme_and_base_path() {
        let c = ApiClient::new("https://api.example.com/prefix").unwrap();
        assert_eq!(
            c.ws_url("/api/v1/meetings/m1/stream"),
            "wss://api.example.com/prefix/api/v1/meetings/m1/stream"
        );
        let local = ApiClient::new("http://localhost:8000").unwrap();
        assert_eq!(
            local.ws_url("/api/v1/meetings/m1/stream"),
            "ws://localhost:8000/api/v1/meetings/m1/stream"
        );
    }

    #[test]
    fn status_mapping_uses_fastapi_detail() {
        assert_eq!(error_for_status(401, "{}"), ApiError::Unauthorized);
        assert_eq!(error_for_status(402, ""), ApiError::QuotaExceeded);
        assert_eq!(
            error_for_status(409, r#"{"detail":"meeting is finished"}"#),
            ApiError::Conflict {
                message: "meeting is finished".into()
            }
        );
        assert!(matches!(
            error_for_status(422, r#"{"detail":[{"msg":"bad"}]}"#),
            ApiError::Rejected { status: 422, .. }
        ));
        assert!(error_for_status(503, "").is_transient());
        assert_eq!(
            error_for_status(503, r#"{"detail":"the AI took too long to answer"}"#),
            ApiError::Unavailable {
                status: 503,
                message: "the AI took too long to answer".into()
            }
        );
        assert!(error_for_status(503, r#"{"detail":"x"}"#).is_transient());
        assert!(!error_for_status(502, r#"{"detail":"declined"}"#).is_transient());
        assert!(!error_for_status(401, "").is_transient());
    }
}
