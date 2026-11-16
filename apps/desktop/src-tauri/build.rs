fn main() {
    link_clang_runtime_for_whisper();
    tauri_build::build()
}

/// whisper.cpp's Metal backend checks the macOS version at run time (`@available`), which
/// needs `___isPlatformVersionAtLeast` from clang's runtime library. The final link of the
/// app does not pull it in on its own when the deployment target (13.0) is older than the
/// APIs being checked, so link `libclang_rt.osx.a` explicitly for builds with local Whisper.
fn link_clang_runtime_for_whisper() {
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os != "macos" || std::env::var_os("CARGO_FEATURE_LOCAL_WHISPER").is_none() {
        return;
    }
    let out = std::process::Command::new("clang")
        .arg("--print-runtime-dir")
        .output();
    match out {
        Ok(o) if o.status.success() => {
            let dir = String::from_utf8_lossy(&o.stdout).trim().to_string();
            println!("cargo:rustc-link-search=native={dir}");
            println!("cargo:rustc-link-lib=static=clang_rt.osx");
        }
        _ => println!("cargo:warning=could not locate clang runtime; Metal Whisper may not link"),
    }
}
