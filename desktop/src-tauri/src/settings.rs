//! App settings that the Rust side needs (server, library mount, CLI command), stored as JSON.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Mutex;

/// Settings persisted in `settings.json` of the app config directory.
#[derive(Serialize, Deserialize, Clone, Default, Debug, PartialEq)]
#[serde(default)]
pub struct Settings {
    /// Base URL of the Rushbase server (passed to the CLI as `RUSHBASE_SERVER_URL`).
    pub server_url: Option<String>,
    /// Local mount of the library root (`RUSHBASE_LIBRARY_ROOT`); also the asset-protocol scope.
    pub library_root: Option<String>,
    /// Command that runs the worker CLI, e.g. `uv run rushbase` in development. Only editable in
    /// `settings.json` or via `RUSHBASE_CLI`: the webview can neither read-modify nor set it.
    pub cli_command: Option<String>,
    /// UI language of the tray menu (`de` or `en`).
    pub language: Option<String>,
}

/// Accept only `http(s)://host[:port][/path]` without whitespace or control characters.
pub fn validate_server_url(url: &str) -> Result<(), String> {
    let rest = url
        .strip_prefix("http://")
        .or_else(|| url.strip_prefix("https://"))
        .ok_or("the server URL must start with http:// or https://")?;
    let host_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let authority = &rest[..host_end];
    if url.len() > 300
        || authority.is_empty()
        || authority.contains('@')
        || url.chars().any(|c| c.is_whitespace() || c.is_control())
    {
        return Err("invalid server URL".into());
    }
    Ok(())
}

impl Settings {
    /// Apply the settings the webview may change (server URL, language) onto `self`.
    ///
    /// The worker command and the library root are never taken from the webview: the first is
    /// config-file/environment only, the second is set by the native folder dialog.
    pub fn with_ui_changes(&self, requested: &Settings) -> Result<Settings, String> {
        let server_url = match requested.server_url.as_deref().map(str::trim) {
            None | Some("") => None,
            Some(url) => {
                validate_server_url(url)?;
                Some(url.trim_end_matches('/').to_string())
            }
        };
        let language = match requested.language.as_deref() {
            None => None,
            Some(code @ ("de" | "en")) => Some(code.to_string()),
            Some(_) => return Err("unsupported language".into()),
        };
        Ok(Settings {
            server_url,
            language,
            ..self.clone()
        })
    }
}

/// Thread-safe settings with a backing file.
pub struct SettingsStore {
    path: PathBuf,
    inner: Mutex<Settings>,
}

impl SettingsStore {
    /// Load the file (missing or unreadable files give the defaults).
    pub fn load(path: PathBuf) -> Self {
        let settings = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        Self {
            path,
            inner: Mutex::new(settings),
        }
    }

    pub fn get(&self) -> Settings {
        self.inner.lock().expect("settings lock").clone()
    }

    /// Replace and persist the settings.
    pub fn set(&self, settings: Settings) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let text = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
        std::fs::write(&self.path, text).map_err(|e| e.to_string())?;
        *self.inner.lock().expect("settings lock") = settings;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_and_defaults() {
        let dir = std::env::temp_dir().join(format!("rb-settings-{}", std::process::id()));
        let path = dir.join("settings.json");
        let store = SettingsStore::load(path.clone());
        assert_eq!(store.get(), Settings::default());
        let wanted = Settings {
            server_url: Some("http://srv".into()),
            ..Settings::default()
        };
        store.set(wanted.clone()).unwrap();
        assert_eq!(SettingsStore::load(path).get(), wanted);
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn server_urls_must_be_plain_http_or_https() {
        for good in [
            "http://srv:8080",
            "https://media.example.org/",
            "http://10.0.0.5",
        ] {
            assert!(validate_server_url(good).is_ok(), "{good}");
        }
        for bad in [
            "",
            "ftp://srv",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://",
            "http://user:pw@srv",
            "http://srv with space",
            "srv:8080",
        ] {
            assert!(validate_server_url(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_webview_cannot_change_the_cli_command_or_library_root() {
        let stored = Settings {
            cli_command: Some("rushbase".into()),
            library_root: Some("/mnt/lib".into()),
            ..Settings::default()
        };
        let requested = Settings {
            server_url: Some("https://srv.example/".into()),
            library_root: Some("/etc".into()),
            cli_command: Some("sh -c evil".into()),
            language: Some("en".into()),
        };
        let merged = stored.with_ui_changes(&requested).unwrap();
        assert_eq!(merged.cli_command.as_deref(), Some("rushbase"));
        assert_eq!(merged.library_root.as_deref(), Some("/mnt/lib"));
        assert_eq!(merged.server_url.as_deref(), Some("https://srv.example"));
        assert!(stored
            .with_ui_changes(&Settings {
                server_url: Some("file:///x".into()),
                ..Settings::default()
            })
            .is_err());
    }

    #[test]
    fn unknown_or_broken_files_fall_back() {
        let dir = std::env::temp_dir().join(format!("rb-settings-bad-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        std::fs::write(&path, "{not json").unwrap();
        assert_eq!(SettingsStore::load(path).get(), Settings::default());
        std::fs::remove_dir_all(dir).ok();
    }
}
