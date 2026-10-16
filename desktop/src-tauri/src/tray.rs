//! System tray with every action in the menu (Linux tray icons emit no click events, ADR-0004).

use std::panic::{catch_unwind, AssertUnwindSafe};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager};

/// Menu labels in the UI language.
pub fn labels(language: &str) -> [&'static str; 4] {
    if language == "en" {
        ["Show Rushbase", "Start processing", "Stop processing", "Quit"]
    } else {
        ["Rushbase anzeigen", "Verarbeiten starten", "Verarbeitung stoppen", "Beenden"]
    }
}

/// Show and focus the main window.
pub fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Create the tray icon. Returns `false` when the platform cannot show one (for example a
/// Linux desktop without an app-indicator host or without `libayatana-appindicator3`).
pub fn build(app: &AppHandle, language: &str, on_quit: fn(&AppHandle)) -> bool {
    let attempt = catch_unwind(AssertUnwindSafe(|| -> Result<(), tauri::Error> {
        let [show, start, stop, quit] = labels(language);
        let items = [
            MenuItem::with_id(app, "show", show, true, None::<&str>)?,
            MenuItem::with_id(app, "start", start, true, None::<&str>)?,
            MenuItem::with_id(app, "stop", stop, true, None::<&str>)?,
            MenuItem::with_id(app, "quit", quit, true, None::<&str>)?,
        ];
        let separator = PredefinedMenuItem::separator(app)?;
        let menu = Menu::with_items(app, &[&items[0], &separator, &items[1], &items[2], &separator, &items[3]])?;
        let mut builder = TrayIconBuilder::with_id("main").menu(&menu).tooltip("Rushbase");
        if let Some(icon) = app.default_window_icon() {
            builder = builder.icon(icon.clone());
        }
        builder
            .on_menu_event(move |app, event| match event.id.as_ref() {
                "show" => show_main(app),
                "start" | "stop" => {
                    let _ = app.emit("tray-action", event.id.as_ref());
                }
                "quit" => on_quit(app),
                _ => {}
            })
            .build(app)?;
        Ok(())
    }));
    matches!(attempt, Ok(Ok(())))
}

#[cfg(test)]
mod tests {
    use super::labels;

    #[test]
    fn labels_follow_the_language() {
        assert_eq!(labels("en")[3], "Quit");
        assert_eq!(labels("de")[3], "Beenden");
        assert_eq!(labels("fr")[0], "Rushbase anzeigen");
    }
}
