pub mod synthetic;

#[cfg(all(
    feature = "native",
    any(windows, target_os = "macos", target_os = "linux")
))]
pub mod cpal_backend;

#[cfg(all(feature = "native", target_os = "macos"))]
pub mod macos;

#[cfg(all(feature = "native", target_os = "macos"))]
pub mod screencapturekit;
