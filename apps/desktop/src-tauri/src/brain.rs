//! Second brain: memories of past meetings and notes, kept on the user's MeetPilot server.
//! The UI calls the `brain/...` API through here so the access token never reaches it.

use serde::Deserialize;

use crate::account::AccountState;
use crate::capture::CommandError;
use tauri::State;

#[derive(Debug, Deserialize)]
pub struct BrainRequest {
    pub method: String,
    pub path: String,
    #[serde(default)]
    pub body: Option<serde_json::Value>,
}

/// Only the second-brain API, with plain path characters (no `..`, no other host).
pub fn allowed(method: &str, path: &str) -> bool {
    matches!(method, "GET" | "POST" | "PATCH" | "DELETE")
        && path.starts_with("brain/")
        && !path.contains("..")
        && path.len() <= 600
        && path
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "/_-?=&%.+~".contains(c))
}

#[tauri::command]
pub async fn brain_request(
    accounts: State<'_, AccountState>,
    request: BrainRequest,
) -> Result<serde_json::Value, CommandError> {
    if !allowed(&request.method, &request.path) {
        return Err(CommandError::new(
            "invalid_request",
            "not a second-brain request",
        ));
    }
    let (client, access) = accounts.access().await?;
    client
        .brain(
            &access,
            &request.method,
            &request.path,
            request.body.as_ref(),
        )
        .await
        .map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_brain_paths_are_allowed() {
        assert!(allowed("GET", "brain/memories?q=acme%20bulk&limit=50"));
        assert!(allowed("DELETE", "brain/memories/abc123"));
        assert!(allowed("POST", "brain/ask"));
        assert!(!allowed("GET", "meetings"));
        assert!(!allowed("GET", "brain/../auth/me"));
        assert!(!allowed("PUT", "brain/memories"));
        assert!(!allowed("GET", "brain/x y"));
        assert!(!allowed("GET", "brain//evil.com@x"));
    }
}
