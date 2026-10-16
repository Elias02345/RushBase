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
    /// Command that runs the worker CLI, e.g. `uv run rushbase` in development.
    pub cli_command: Option<String>,
    /// UI language of the tray menu (`de` or `en`).
    pub language: Option<String>,
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
    fn unknown_or_broken_files_fall_back() {
        let dir = std::env::temp_dir().join(format!("rb-settings-bad-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        std::fs::write(&path, "{not json").unwrap();
        assert_eq!(SettingsStore::load(path).get(), Settings::default());
        std::fs::remove_dir_all(dir).ok();
    }
}
