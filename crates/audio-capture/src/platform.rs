//! Selects the capture provider for the current OS.

use std::sync::Arc;

use crate::provider::AudioCaptureProvider;

/// System-audio backend on macOS, chosen by OS version (see `providers::macos`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MacSystemBackend {
    ProcessTap,
    ScreenCaptureKit,
    Unsupported,
}

/// Pure selection logic, kept platform-independent so it is unit-tested on every OS.
pub fn choose_macos_backend(
    version: (isize, isize, isize),
    forced: Option<&str>,
) -> MacSystemBackend {
    let (major, minor, _) = version;
    let tap_ok = major > 14 || (major == 14 && minor >= 6);
    let sck_ok = major >= 13;
    match forced {
        Some("sck") if sck_ok => MacSystemBackend::ScreenCaptureKit,
        Some("tap") if tap_ok => MacSystemBackend::ProcessTap,
        _ if tap_ok => MacSystemBackend::ProcessTap,
        _ if sck_ok => MacSystemBackend::ScreenCaptureKit,
        _ => MacSystemBackend::Unsupported,
    }
}

/// The real OS provider when the `native` feature is enabled, otherwise `None`.
pub fn default_provider() -> Option<Arc<dyn AudioCaptureProvider>> {
    #[cfg(all(feature = "native", target_os = "macos"))]
    {
        return Some(Arc::new(crate::providers::macos::MacProvider::new()));
    }
    #[cfg(all(feature = "native", any(windows, target_os = "linux")))]
    {
        return Some(Arc::new(
            crate::providers::cpal_backend::CpalProvider::for_current_platform(),
        ));
    }
    #[allow(unreachable_code)]
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn macos_backend_selection_by_version() {
        use MacSystemBackend::*;
        assert_eq!(choose_macos_backend((15, 0, 0), None), ProcessTap);
        assert_eq!(choose_macos_backend((14, 6, 0), None), ProcessTap);
        assert_eq!(choose_macos_backend((14, 5, 1), None), ScreenCaptureKit);
        assert_eq!(choose_macos_backend((13, 0, 0), None), ScreenCaptureKit);
        assert_eq!(choose_macos_backend((12, 7, 0), None), Unsupported);
        assert_eq!(
            choose_macos_backend((15, 0, 0), Some("sck")),
            ScreenCaptureKit
        );
        // Forcing the tap on an OS that lacks it falls back to what works.
        assert_eq!(
            choose_macos_backend((13, 0, 0), Some("tap")),
            ScreenCaptureKit
        );
    }
}
