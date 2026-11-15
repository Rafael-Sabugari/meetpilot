//! Signed-in account. Tokens are kept in memory only (never written to disk or logs); the
//! user signs in again after restarting the app. OS keychain storage is planned.

use std::time::{Duration, Instant};

use meetpilot_realtime::{ApiClient, ApiError, Me, Usage};
use serde::{Deserialize, Serialize};
use tauri::State;
use tokio::sync::Mutex;

use crate::capture::CommandError;

/// Refresh the access token when it has less than this left.
const REFRESH_MARGIN: Duration = Duration::from_secs(60);

struct Account {
    client: ApiClient,
    me: Me,
    access: String,
    access_expires: Instant,
    refresh: String,
}

#[derive(Default)]
pub struct AccountState {
    inner: Mutex<Option<Account>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AccountInfo {
    pub email: String,
    pub plan: String,
    pub server: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Credentials {
    pub server: String,
    pub email: String,
    pub password: String,
}

impl From<ApiError> for CommandError {
    fn from(e: ApiError) -> Self {
        let kind = serde_json::to_value(&e)
            .ok()
            .and_then(|v| v.get("kind").and_then(|k| k.as_str()).map(str::to_owned))
            .unwrap_or_else(|| "api_error".into());
        CommandError::new(&kind, e.to_string())
    }
}

fn info(a: &Account) -> AccountInfo {
    AccountInfo {
        email: a.me.email.clone(),
        plan: a.me.plan.clone(),
        server: a.client.base_url().to_string(),
    }
}

impl AccountState {
    /// A valid access token (refreshed if close to expiry) and the client to use it with.
    pub async fn access(&self) -> Result<(ApiClient, String), CommandError> {
        let mut guard = self.inner.lock().await;
        let acct = guard.as_mut().ok_or_else(|| {
            CommandError::new(
                "not_signed_in",
                "Sign in to MeetPilot to use transcription.",
            )
        })?;
        if acct
            .access_expires
            .saturating_duration_since(Instant::now())
            < REFRESH_MARGIN
        {
            match acct.client.refresh(&acct.refresh).await {
                Ok(pair) => {
                    acct.access = pair.access_token;
                    acct.access_expires = Instant::now() + Duration::from_secs(pair.expires_in);
                    acct.refresh = pair.refresh_token;
                }
                Err(ApiError::Unauthorized) => {
                    *guard = None;
                    return Err(CommandError::new(
                        "not_signed_in",
                        "Your session expired. Sign in again.",
                    ));
                }
                Err(e) => return Err(e.into()),
            }
        }
        Ok((acct.client.clone(), acct.access.clone()))
    }
}

async fn sign_in(
    state: &AccountState,
    client: ApiClient,
    email: &str,
    password: &str,
) -> Result<AccountInfo, CommandError> {
    let pair = client.login(email, password).await?;
    let me = client.me(&pair.access_token).await?;
    let acct = Account {
        client,
        me,
        access_expires: Instant::now() + Duration::from_secs(pair.expires_in),
        access: pair.access_token,
        refresh: pair.refresh_token,
    };
    let out = info(&acct);
    *state.inner.lock().await = Some(acct);
    Ok(out)
}

#[tauri::command]
pub async fn account_login(
    state: State<'_, AccountState>,
    credentials: Credentials,
) -> Result<AccountInfo, CommandError> {
    let client = ApiClient::new(&credentials.server)?;
    sign_in(
        &state,
        client,
        credentials.email.trim(),
        &credentials.password,
    )
    .await
}

#[tauri::command]
pub async fn account_register(
    state: State<'_, AccountState>,
    credentials: Credentials,
) -> Result<AccountInfo, CommandError> {
    let client = ApiClient::new(&credentials.server)?;
    let email = credentials.email.trim();
    client.register(email, &credentials.password).await?;
    sign_in(&state, client, email, &credentials.password).await
}

#[tauri::command]
pub async fn account_logout(state: State<'_, AccountState>) -> Result<(), CommandError> {
    let acct = state.inner.lock().await.take();
    if let Some(a) = acct {
        // Best effort: revoke server-side; local tokens are dropped regardless.
        let _ = a.client.logout(&a.refresh).await;
    }
    Ok(())
}

#[tauri::command]
pub async fn account_status(
    state: State<'_, AccountState>,
) -> Result<Option<AccountInfo>, CommandError> {
    Ok(state.inner.lock().await.as_ref().map(info))
}

#[tauri::command]
pub async fn account_usage(state: State<'_, AccountState>) -> Result<Usage, CommandError> {
    let (client, access) = state.access().await?;
    Ok(client.usage(&access).await?)
}
