//! Signed-in account. The access token lives in memory only. To stay signed in between
//! launches, the server URL and the (rotating) refresh token are kept in the OS credential
//! store (macOS Keychain / Windows Credential Manager) — never in files or logs.

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

/// What is remembered between launches.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SavedSession {
    pub server: String,
    pub refresh: String,
}

/// Where the saved session lives. The real one is the OS keychain; tests use memory.
pub trait SessionStore: Send + Sync {
    fn load(&self) -> Option<SavedSession>;
    fn save(&self, s: &SavedSession);
    fn clear(&self);
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
pub struct KeychainStore;

#[cfg(any(target_os = "macos", target_os = "windows"))]
impl KeychainStore {
    fn entry() -> Option<keyring::Entry> {
        keyring::Entry::new("app.meetpilot.desktop", "session").ok()
    }
}

#[cfg(any(target_os = "macos", target_os = "windows"))]
impl SessionStore for KeychainStore {
    fn load(&self) -> Option<SavedSession> {
        let raw = Self::entry()?.get_password().ok()?;
        serde_json::from_str(&raw).ok()
    }
    fn save(&self, s: &SavedSession) {
        if let (Some(e), Ok(raw)) = (Self::entry(), serde_json::to_string(s)) {
            if e.set_password(&raw).is_err() {
                log::warn!("could not save the session to the keychain");
            }
        }
    }
    fn clear(&self) {
        if let Some(e) = Self::entry() {
            let _ = e.delete_credential();
        }
    }
}

/// Process memory only (Linux development builds and tests).
#[derive(Default)]
#[cfg_attr(any(target_os = "macos", target_os = "windows"), allow(dead_code))]
pub struct MemoryStore(std::sync::Mutex<Option<SavedSession>>);

impl SessionStore for MemoryStore {
    fn load(&self) -> Option<SavedSession> {
        self.0.lock().ok()?.clone()
    }
    fn save(&self, s: &SavedSession) {
        if let Ok(mut g) = self.0.lock() {
            *g = Some(s.clone());
        }
    }
    fn clear(&self) {
        if let Ok(mut g) = self.0.lock() {
            *g = None;
        }
    }
}

pub struct AccountState {
    inner: Mutex<Option<Account>>,
    store: Box<dyn SessionStore>,
    /// Attempts to reach the server when restoring a saved session (it may still be starting).
    restore_attempts: u32,
    restore_delay: Duration,
}

impl Default for AccountState {
    fn default() -> Self {
        #[cfg(any(target_os = "macos", target_os = "windows"))]
        let store: Box<dyn SessionStore> = Box::new(KeychainStore);
        #[cfg(not(any(target_os = "macos", target_os = "windows")))]
        let store: Box<dyn SessionStore> = Box::new(MemoryStore::default());
        Self::with_store(store, 6, Duration::from_secs(2))
    }
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
    pub fn with_store(store: Box<dyn SessionStore>, attempts: u32, delay: Duration) -> Self {
        Self {
            inner: Mutex::new(None),
            store,
            restore_attempts: attempts.max(1),
            restore_delay: delay,
        }
    }

    fn remember(&self, acct: &Account) {
        self.store.save(&SavedSession {
            server: acct.client.base_url().to_string(),
            refresh: acct.refresh.clone(),
        });
    }

    /// Restore the saved session, if any. Holds the lock throughout so two windows asking at
    /// startup never present the same (single-use) refresh token twice.
    async fn restore(&self, guard: &mut Option<Account>) {
        if guard.is_some() {
            return;
        }
        let Some(saved) = self.store.load() else {
            return;
        };
        let Ok(client) = ApiClient::new(&saved.server) else {
            self.store.clear();
            return;
        };
        for attempt in 0..self.restore_attempts {
            match client.refresh(&saved.refresh).await {
                Ok(pair) => {
                    // Persist the rotated token first: the old one is now spent.
                    self.store.save(&SavedSession {
                        server: saved.server.clone(),
                        refresh: pair.refresh_token.clone(),
                    });
                    let Ok(me) = client.me(&pair.access_token).await else {
                        return; // keep the saved session; try again next time
                    };
                    *guard = Some(Account {
                        client,
                        me,
                        access_expires: Instant::now() + Duration::from_secs(pair.expires_in),
                        access: pair.access_token,
                        refresh: pair.refresh_token,
                    });
                    return;
                }
                Err(ApiError::Unauthorized) => {
                    // Revoked or expired: forget it, the user signs in again.
                    self.store.clear();
                    return;
                }
                Err(_) if attempt + 1 < self.restore_attempts => {
                    // Server not up yet (e.g. right after login): wait and retry.
                    tokio::time::sleep(self.restore_delay).await;
                }
                Err(_) => return, // keep the saved session for the next launch
            }
        }
    }

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
                    self.remember(acct);
                }
                Err(ApiError::Unauthorized) => {
                    *guard = None;
                    self.store.clear();
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
    state.remember(&acct);
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
    state.store.clear();
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
    let mut guard = state.inner.lock().await;
    state.restore(&mut guard).await;
    Ok(guard.as_ref().map(info))
}

#[tauri::command]
pub async fn account_usage(state: State<'_, AccountState>) -> Result<Usage, CommandError> {
    let (client, access) = state.access().await?;
    Ok(client.usage(&access).await?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    struct Shared(Arc<MemoryStore>);
    impl SessionStore for Shared {
        fn load(&self) -> Option<SavedSession> {
            self.0.load()
        }
        fn save(&self, s: &SavedSession) {
            self.0.save(s)
        }
        fn clear(&self) {
            self.0.clear()
        }
    }

    fn saved(server: &str) -> SavedSession {
        SavedSession {
            server: server.into(),
            refresh: "r".repeat(40),
        }
    }

    #[test]
    fn saved_session_round_trips_as_json() {
        let s = saved("http://localhost:8000/");
        let raw = serde_json::to_string(&s).unwrap();
        assert_eq!(serde_json::from_str::<SavedSession>(&raw).unwrap(), s);
    }

    #[tokio::test]
    async fn unreachable_server_keeps_the_saved_session_for_later() {
        let mem = Arc::new(MemoryStore::default());
        mem.save(&saved("http://127.0.0.1:9/"));
        let st = AccountState::with_store(Box::new(Shared(mem.clone())), 2, Duration::ZERO);
        let mut g = st.inner.lock().await;
        st.restore(&mut g).await;
        assert!(g.is_none(), "not signed in while the server is down");
        assert!(
            mem.load().is_some(),
            "but the session is kept for the next try"
        );
    }

    #[tokio::test]
    async fn invalid_saved_server_is_forgotten() {
        let mem = Arc::new(MemoryStore::default());
        mem.save(&saved("not a url"));
        let st = AccountState::with_store(Box::new(Shared(mem.clone())), 1, Duration::ZERO);
        let mut g = st.inner.lock().await;
        st.restore(&mut g).await;
        assert!(mem.load().is_none());
    }

    #[tokio::test]
    async fn nothing_saved_means_signed_out() {
        let st = AccountState::with_store(Box::new(MemoryStore::default()), 1, Duration::ZERO);
        let mut g = st.inner.lock().await;
        st.restore(&mut g).await;
        assert!(g.is_none());
    }
}
