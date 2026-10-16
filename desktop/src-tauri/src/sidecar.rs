//! Running the Python worker CLI (`rushbase`): resolution of the command and one-shot calls.

use crate::settings::Settings;
use serde::Serialize;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;

/// Sub-commands the UI may run (anything else is rejected before spawning).
pub const ALLOWED: &[&str] = &[
    "version", "hardware", "overview", "doctor", "status", "models", "login", "enroll", "search",
    "browse", "filters", "asset", "select", "paths", "export", "resolve",
];

/// Append-only log of CLI calls (used by the smoke test and for support).
pub struct Logger {
    path: Mutex<Option<PathBuf>>,
}

impl Logger {
    pub fn new(path: Option<PathBuf>) -> Self {
        if let Some(parent) = path.as_ref().and_then(|p| p.parent()) {
            let _ = std::fs::create_dir_all(parent);
        }
        Self { path: Mutex::new(path) }
    }

    pub fn line(&self, text: &str) {
        let guard = self.path.lock().expect("log lock");
        if let Some(path) = guard.as_ref() {
            if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                let _ = writeln!(file, "{now} {text}");
            }
        }
    }
}

/// Result of a finished CLI call.
#[derive(Serialize, Debug)]
pub struct CliOutput {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

/// The command prefix: `RUSHBASE_CLI`, the configured command, the bundled sidecar next to the
/// executable, or `rushbase` from `PATH`.
pub fn base_command(settings: &Settings) -> Vec<String> {
    let configured = std::env::var("RUSHBASE_CLI")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| settings.cli_command.clone().filter(|v| !v.trim().is_empty()));
    if let Some(text) = configured {
        if let Some(parts) = shlex::split(&text) {
            if !parts.is_empty() {
                return parts;
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let name = if cfg!(windows) { "rushbase.exe" } else { "rushbase" };
        let sibling = exe.with_file_name(name);
        if sibling.is_file() {
            return vec![sibling.to_string_lossy().into_owned()];
        }
    }
    vec!["rushbase".to_string()]
}

/// Whether `args` start with an allowed sub-command.
pub fn is_allowed(args: &[String]) -> bool {
    args.first().is_some_and(|first| ALLOWED.contains(&first.as_str()))
}

/// Build the process for `args` with the configured environment.
pub fn build(settings: &Settings, args: &[String]) -> Command {
    let base = base_command(settings);
    let mut command = Command::new(&base[0]);
    command.args(&base[1..]).args(args);
    if let Some(url) = settings.server_url.as_ref().filter(|v| !v.is_empty()) {
        command.env("RUSHBASE_SERVER_URL", url);
    }
    if let Some(root) = settings.library_root.as_ref().filter(|v| !v.is_empty()) {
        command.env("RUSHBASE_LIBRARY_ROOT", root);
    }
    command.env("PYTHONUNBUFFERED", "1").env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    command
}

/// Run an allowed sub-command to completion, optionally feeding `stdin` (passwords).
pub fn run_cli(
    settings: &Settings,
    log: &Logger,
    args: &[String],
    stdin: Option<String>,
) -> Result<CliOutput, String> {
    if !is_allowed(args) {
        return Err(format!("command not allowed: {}", args.first().cloned().unwrap_or_default()));
    }
    // Arguments may contain secrets only on stdin; the log shows the sub-command path only.
    log.line(&format!("cli {}", args.iter().take(2).cloned().collect::<Vec<_>>().join(" ")));
    let mut command = build(settings, args);
    command.stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() });
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|e| format!("cannot start the worker CLI: {e}"))?;
    if let (Some(text), Some(mut pipe)) = (stdin, child.stdin.take()) {
        pipe.write_all(text.as_bytes()).map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    Ok(CliOutput {
        code: output.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn only_listed_subcommands_are_allowed() {
        assert!(is_allowed(&args(&["hardware", "--json"])));
        assert!(is_allowed(&args(&["models", "list"])));
        assert!(!is_allowed(&args(&["process", "--json"])));
        assert!(!is_allowed(&args(&["mcp", "serve"])));
        assert!(!is_allowed(&args(&[])));
        assert!(!is_allowed(&args(&["; rm -rf /"])));
    }

    #[test]
    fn configured_command_is_split_like_a_shell() {
        let settings = Settings { cli_command: Some("uv run --project '/a b' rushbase".into()), ..Settings::default() };
        if std::env::var("RUSHBASE_CLI").is_err() {
            assert_eq!(base_command(&settings), vec!["uv", "run", "--project", "/a b", "rushbase"]);
        }
    }

    #[test]
    fn builds_environment_from_settings() {
        let settings = Settings {
            server_url: Some("http://srv:8080".into()),
            library_root: Some("/mnt/lib".into()),
            ..Settings::default()
        };
        let command = build(&settings, &args(&["hardware"]));
        let envs: Vec<_> = command.get_envs().filter_map(|(k, v)| Some((k.to_str()?, v?.to_str()?))).collect();
        assert!(envs.contains(&("RUSHBASE_SERVER_URL", "http://srv:8080")));
        assert!(envs.contains(&("RUSHBASE_LIBRARY_ROOT", "/mnt/lib")));
    }

    #[test]
    fn disallowed_calls_never_spawn() {
        let log = Logger::new(None);
        let result = run_cli(&Settings::default(), &log, &args(&["process"]), None);
        assert!(result.is_err());
    }
}
