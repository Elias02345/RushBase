//! Build script: Tauri codegen plus a release-build guard for the updater signing key.

fn main() {
    println!("cargo:rerun-if-env-changed=RUSHBASE_UPDATER_PUBKEY");
    let release = std::env::var("PROFILE").is_ok_and(|profile| profile == "release");
    if release {
        guard_updater_key(std::env::var("RUSHBASE_UPDATER_PUBKEY").ok().as_deref());
    }
    tauri_build::build()
}

/// A release build with the updater enabled must carry a real public key: the placeholder in
/// `tauri.conf.json` would make the app trust updates signed with a key nobody controls.
fn guard_updater_key(key: Option<&str>) {
    if let Some(key) = key {
        assert!(
            !key.trim().is_empty() && !key.contains("PLACEHOLDER"),
            "RUSHBASE_UPDATER_PUBKEY is empty or still the placeholder: refusing to build a \
             release with the updater enabled (docs/companion.md)"
        );
    }
}
