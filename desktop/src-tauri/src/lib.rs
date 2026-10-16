//! Rushbase companion app (Tauri 2). The Python worker runs as a sidecar; this crate owns the
//! window, tray, process lifecycle (graceful release on quit) and a few platform probes.

mod settings;
mod sidecar;
mod streams;
mod system;
mod tray;

use settings::{Settings, SettingsStore};
use sidecar::{CliOutput, Logger};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use streams::Streams;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

/// How long quitting waits for the worker to release its jobs.
const QUIT_GRACE: Duration = Duration::from_secs(25);

struct App {
    settings: SettingsStore,
    log: Arc<Logger>,
    streams: Streams,
    has_tray: AtomicBool,
    quitting: AtomicBool,
}

type Shared<'a> = State<'a, App>;

#[tauri::command]
fn get_settings(app: Shared) -> Settings {
    app.settings.get()
}

#[tauri::command]
fn set_settings(handle: AppHandle, app: Shared, settings: Settings) -> Result<(), String> {
    app.settings.set(settings.clone())?;
    allow_library(&handle, &settings);
    Ok(())
}

/// Run an allowed CLI sub-command to completion (stdin only for secrets such as passwords).
#[tauri::command]
async fn cli(
    app: Shared<'_>,
    args: Vec<String>,
    stdin: Option<String>,
) -> Result<CliOutput, String> {
    let settings = app.settings.get();
    let log = Arc::clone(&app.log);
    tauri::async_runtime::spawn_blocking(move || sidecar::run_cli(&settings, &log, &args, stdin))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn stream_start(
    handle: AppHandle,
    app: Shared,
    name: String,
    options: Vec<String>,
) -> Result<u32, String> {
    app.streams
        .start(&handle, &app.settings.get(), &app.log, &name, &options)
}

#[tauri::command]
fn stream_stop(app: Shared, name: String) -> bool {
    app.streams.stop(&name)
}

#[tauri::command]
fn stream_running(app: Shared, name: String) -> bool {
    app.streams.is_running(&name)
}

#[tauri::command]
fn gpu_stats() -> Option<system::GpuStats> {
    system::gpu_stats()
}

#[tauri::command]
fn idle_seconds() -> Option<u64> {
    system::idle_seconds()
}

#[tauri::command]
fn drag_check(path: String) -> system::DragCheck {
    let mounts = std::fs::read_to_string("/proc/mounts").ok();
    system::drag_check(&path, mounts.as_deref())
}

#[tauri::command]
fn platform_info(app: Shared) -> serde_json::Value {
    let mut info = serde_json::to_value(system::platform_info()).unwrap_or_default();
    info["tray"] = app.has_tray.load(Ordering::Relaxed).into();
    info["updater"] = option_env!("RUSHBASE_UPDATER_PUBKEY").is_some().into();
    info
}

/// Graceful quit: stop the worker (jobs are released) and exit.
#[tauri::command]
fn quit_app(handle: AppHandle) {
    request_quit(&handle);
}

fn allow_library(handle: &AppHandle, settings: &Settings) {
    if let Some(root) = settings.library_root.as_ref().filter(|r| !r.is_empty()) {
        // Read-only by construction: the asset protocol only serves files.
        let _ = handle.asset_protocol_scope().allow_directory(root, true);
    }
}

fn request_quit(handle: &AppHandle) {
    let state = handle.state::<App>();
    if state.quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    let handle = handle.clone();
    std::thread::spawn(move || {
        let state = handle.state::<App>();
        state.log.line("quit: stopping streams");
        state.streams.stop_all_blocking(QUIT_GRACE);
        handle.exit(0);
    });
}

fn startup_check(handle: AppHandle) {
    std::thread::spawn(move || {
        let state = handle.state::<App>();
        let settings = state.settings.get();
        for args in [
            vec!["version".to_string(), "--json".into()],
            vec!["hardware".into(), "--json".into()],
        ] {
            let outcome = sidecar::run_cli(&settings, &state.log, &args, None);
            let note = match outcome {
                Ok(out) => format!("exit {}", out.code),
                Err(e) => format!("failed: {e}"),
            };
            state
                .log
                .line(&format!("startup check {} -> {note}", args[0]));
        }
    });
}

/// webkit2gtk renders a blank window on Wayland with the proprietary NVIDIA driver unless its
/// DMA-BUF renderer is off. Only set when the user has not chosen (docs/companion.md).
fn apply_linux_webkit_workarounds() {
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none()
        && system::is_wayland()
        && std::path::Path::new("/proc/driver/nvidia").exists()
    {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
}

/// Build and run the application.
pub fn run() {
    apply_linux_webkit_workarounds();
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            tray::show_main(app)
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--minimized"]),
        ))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_drag::init());

    // The updater only exists in builds made with a signing public key (docs/companion.md).
    if let Some(key) = option_env!("RUSHBASE_UPDATER_PUBKEY") {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().pubkey(key).build());
    }

    builder
        .invoke_handler(tauri::generate_handler![
            get_settings,
            set_settings,
            cli,
            stream_start,
            stream_stop,
            stream_running,
            gpu_stats,
            idle_seconds,
            drag_check,
            platform_info,
            quit_app
        ])
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let store = SettingsStore::load(config_dir.join("settings.json"));
            let log_path = std::env::var_os("RUSHBASE_DESKTOP_LOG")
                .map(std::path::PathBuf::from)
                .or_else(|| app.path().app_log_dir().ok().map(|d| d.join("desktop.log")));
            let settings = store.get();
            let language = settings.language.clone().unwrap_or_else(|| "de".into());
            let state = App {
                settings: store,
                log: Arc::new(Logger::new(log_path)),
                streams: Streams::default(),
                has_tray: AtomicBool::new(false),
                quitting: AtomicBool::new(false),
            };
            state.log.line("app started");
            app.manage(state);
            allow_library(app.handle(), &settings);
            let has_tray = tray::build(app.handle(), &language, request_quit);
            app.state::<App>()
                .has_tray
                .store(has_tray, Ordering::Relaxed);
            app.state::<App>()
                .log
                .line(&format!("tray available: {has_tray}"));
            if std::env::args().any(|a| a == "--minimized") && has_tray {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
            }
            startup_check(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let state = window.app_handle().state::<App>();
                // With a tray the window hides and processing continues; without one, closing quits.
                if state.has_tray.load(Ordering::Relaxed) && !state.quitting.load(Ordering::Relaxed)
                {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the Rushbase companion")
        .run(|handle, event| {
            if let RunEvent::ExitRequested { api, .. } = event {
                let state = handle.state::<App>();
                if !state.quitting.load(Ordering::SeqCst) {
                    api.prevent_exit();
                    request_quit(handle);
                }
            }
        });
}
