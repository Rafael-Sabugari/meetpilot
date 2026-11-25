//! MeetPilot desktop shell.
//!
//! MeetPilot is a private desktop copilot. It captures audio that is already playing on the
//! user's own computer, only after explicit user action, and shows results only to that
//! user. It never joins a meeting as a bot, participant or attendee, and it does not use
//! meeting links, invitations or meeting-provider APIs.

mod account;
mod brain;
mod capture;
mod copilot;
mod local;

use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

use account::AccountState;
use capture::CaptureState;

pub const EVT_SHORTCUT_ERROR: &str = "app://shortcut-error";
pub const EVT_RESPOND: &str = "app://respond";
/// The shortcut assistant window was opened: focus its input.
pub const EVT_ASSISTANT_OPEN: &str = "app://assistant-open";
pub const ASSISTANT_LABEL: &str = "assistant";

/// Show the assistant (focused, ready to type) or hide it if it is already in front.
fn toggle_assistant(app: &tauri::AppHandle, force_show: bool) {
    let Some(w) = app.get_webview_window(ASSISTANT_LABEL) else {
        return;
    };
    let visible = w.is_visible().unwrap_or(false);
    let focused = w.is_focused().unwrap_or(false);
    if visible && focused && !force_show {
        let _ = w.hide();
        return;
    }
    let _ = w.show();
    let _ = w.unminimize();
    let _ = w.set_focus();
    let _ = app.emit_to(ASSISTANT_LABEL, EVT_ASSISTANT_OPEN, ());
}

#[tauri::command]
fn show_assistant(app: tauri::AppHandle) {
    toggle_assistant(&app, true);
}

fn primary_modifier() -> Modifiers {
    if cfg!(target_os = "macos") {
        Modifiers::SUPER | Modifiers::SHIFT
    } else {
        Modifiers::CONTROL | Modifiers::SHIFT
    }
}

pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    // Ctrl/Cmd+Shift+M: pause/resume capture. Ctrl/Cmd+Shift+O: show/hide overlay.
    let pause_shortcut = Shortcut::new(Some(primary_modifier()), Code::KeyM);
    let overlay_shortcut = Shortcut::new(Some(primary_modifier()), Code::KeyO);
    // Ctrl/Cmd+Shift+R: ask for a reply to what the others said since the last send.
    let respond_shortcut = Shortcut::new(Some(primary_modifier()), Code::KeyR);
    // Ctrl/Cmd+Shift+Space: open the assistant (ask anything, by text) from any app.
    let assistant_shortcut = Shortcut::new(Some(primary_modifier()), Code::Space);

    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    if shortcut == &pause_shortcut {
                        let state = app.state::<CaptureState>();
                        match capture::toggle_pause(&state) {
                            Ok(s) => {
                                let _ = app.emit(capture::EVT_STATUS, s.status);
                            }
                            Err(e) => log::info!("pause shortcut ignored: {}", e.message),
                        }
                    } else if shortcut == &respond_shortcut {
                        // The overlay handles it; show it so the answer is visible.
                        capture::set_overlay_visible(app, true);
                        let _ = app.emit_to("overlay", EVT_RESPOND, ());
                    } else if shortcut == &assistant_shortcut {
                        toggle_assistant(app, false);
                    } else if shortcut == &overlay_shortcut {
                        if let Some(w) = app.get_webview_window(capture::OVERLAY_LABEL) {
                            let visible = w.is_visible().unwrap_or(false);
                            capture::set_overlay_visible(app, !visible);
                        }
                    }
                })
                .build(),
        )
        .manage(CaptureState::default())
        .manage(AccountState::default())
        .setup(move |app| {
            app.manage(local::LocalState::new(app.handle()));
            for sc in [
                pause_shortcut,
                overlay_shortcut,
                respond_shortcut,
                assistant_shortcut,
            ] {
                if let Err(e) = app.global_shortcut().register(sc) {
                    // Another app may own the shortcut; the app stays fully usable without it.
                    log::warn!("could not register global shortcut {sc}: {e}");
                    let _ = app.emit(EVT_SHORTCUT_ERROR, sc.to_string());
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the main window stops capture: nothing keeps recording in the background.
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { .. } = event {
                    let app = window.app_handle();
                    capture::stop_for_exit(app);
                    app.exit(0);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            capture::get_capabilities,
            capture::list_devices,
            capture::request_permission,
            capture::start_capture,
            capture::pause_capture,
            capture::resume_capture,
            capture::stop_capture,
            capture::get_capture_status,
            capture::set_overlay_protection,
            capture::request_reply,
            capture::set_overlay_shown,
            capture::set_live_options,
            account::account_login,
            account::account_register,
            account::account_logout,
            account::account_status,
            account::account_usage,
            local::local_models,
            local::local_download,
            local::local_cancel_download,
            local::local_delete_model,
            copilot::ask_copilot,
            brain::brain_request,
            show_assistant,
        ])
        .run(tauri::generate_context!())
        .expect("error while running MeetPilot");
}
