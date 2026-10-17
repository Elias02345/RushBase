//! Running the Python worker CLI (`rushbase`): resolution of the command and one-shot calls.

use crate::settings::Settings;
use serde::Serialize;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;

/// A sub-command the UI may run with the flags it may pass.
struct Spec {
    name: &'static str,
    /// Flags without a value.
    flags: &'static [&'static str],
    /// Flags followed by exactly one value.
    value_flags: &'static [&'static str],
}

const fn spec(
    name: &'static str,
    flags: &'static [&'static str],
    value_flags: &'static [&'static str],
) -> Spec {
    Spec {
        name,
        flags,
        value_flags,
    }
}

/// Everything the UI may run (anything else is rejected before spawning): sub-command and flags.
const SPECS: &[Spec] = &[
    spec("version", &["--json"], &[]),
    spec("hardware", &["--json"], &[]),
    spec("overview", &["--json"], &[]),
    spec("doctor", &["--json"], &[]),
    spec("status", &["--json"], &[]),
    spec("filters", &["--json"], &[]),
    spec("paths", &["--json"], &[]),
    spec("asset", &["--json"], &[]),
    spec("models", &["--json", "--all"], &["--profile"]),
    spec(
        "login",
        &["--json", "--password-stdin"],
        &["--server", "--username"],
    ),
    spec("enroll", &["--json"], &["--server", "--token", "--name"]),
    spec("search", &["--json"], &["--limit", "--offset", "--filter"]),
    spec(
        "browse",
        &["--json"],
        &["--limit", "--sort", "--cursor", "--filter"],
    ),
    spec("select", &["--json"], &["--shots"]),
    spec("export", &["--json"], &["--out"]),
    spec(
        "resolve",
        &["--json", "--yes", "--markers", "--metadata"],
        &["--bin"],
    ),
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
        Self {
            path: Mutex::new(path),
        }
    }

    pub fn line(&self, text: &str) {
        let guard = self.path.lock().expect("log lock");
        if let Some(path) = guard.as_ref() {
            if let Ok(mut file) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
            {
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
        .or_else(|| {
            settings
                .cli_command
                .clone()
                .filter(|v| !v.trim().is_empty())
        });
    if let Some(text) = configured {
        if let Some(parts) = shlex::split(&text) {
            if !parts.is_empty() {
                return parts;
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let name = if cfg!(windows) {
            "rushbase.exe"
        } else {
            "rushbase"
        };
        let sibling = exe.with_file_name(name);
        if sibling.is_file() {
            return vec![sibling.to_string_lossy().into_owned()];
        }
    }
    vec!["rushbase".to_string()]
}

/// Check `args` against the allow-list: known sub-command, only its listed flags, and flags that
/// take a value must have one. Anything else starting with `-` is rejected.
pub fn validate(args: &[String]) -> Result<(), String> {
    let first = args.first().ok_or("no command given")?;
    let spec = SPECS
        .iter()
        .find(|s| s.name == first)
        .ok_or_else(|| format!("command not allowed: {first}"))?;
    let mut rest = args[1..].iter();
    while let Some(arg) = rest.next() {
        if arg.len() > 2000 {
            return Err("argument too long".into());
        }
        if spec.flags.contains(&arg.as_str()) {
            continue;
        }
        if spec.value_flags.contains(&arg.as_str()) {
            rest.next().ok_or_else(|| format!("{arg} needs a value"))?;
        } else if arg.starts_with('-') {
            return Err(format!("flag not allowed for {first}: {arg}"));
        }
    }
    Ok(())
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
    command
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8");
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
    validate(args)?;
    // Arguments may contain secrets only on stdin; the log shows the sub-command path only.
    log.line(&format!(
        "cli {}",
        args.iter().take(2).cloned().collect::<Vec<_>>().join(" ")
    ));
    let mut command = build(settings, args);
    command.stdin(if stdin.is_some() {
        Stdio::piped()
    } else {
        Stdio::null()
    });
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|e| format!("cannot start the worker CLI: {e}"))?;
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
        assert!(validate(&args(&["hardware", "--json"])).is_ok());
        assert!(validate(&args(&["models", "list"])).is_ok());
        assert!(validate(&args(&["process", "--json"])).is_err());
        assert!(validate(&args(&["mcp", "serve"])).is_err());
        assert!(validate(&args(&[])).is_err());
        assert!(validate(&args(&["; rm -rf /"])).is_err());
    }

    #[test]
    fn flags_are_allow_listed_per_subcommand() {
        assert!(validate(&args(&[
            "search", "harbour", "--limit", "40", "--filter", "a=b"
        ]))
        .is_ok());
        assert!(validate(&args(&["select", "add", "abc", "--shots", "1,2", "--json"])).is_ok());
        assert!(validate(&args(&[
            "login",
            "--server",
            "http://s",
            "--username",
            "u",
            "--password-stdin"
        ]))
        .is_ok());
        // flags of other commands, global options and unknown flags are refused
        assert!(validate(&args(&["search", "x", "--out", "/tmp/x"])).is_err());
        assert!(validate(&args(&["overview", "--library", "/etc"])).is_err());
        assert!(validate(&args(&["doctor", "--repair"])).is_err());
        assert!(validate(&args(&["export", "csv", "--out"])).is_err()); // missing value
        assert!(validate(&args(&["version", "--help"])).is_err());
        assert!(validate(&args(&["asset", "get", "--config=/x"])).is_err());
    }

    #[test]
    fn configured_command_is_split_like_a_shell() {
        let settings = Settings {
            cli_command: Some("uv run --project '/a b' rushbase".into()),
            ..Settings::default()
        };
        if std::env::var("RUSHBASE_CLI").is_err() {
            assert_eq!(
                base_command(&settings),
                vec!["uv", "run", "--project", "/a b", "rushbase"]
            );
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
        let envs: Vec<_> = command
            .get_envs()
            .filter_map(|(k, v)| Some((k.to_str()?, v?.to_str()?)))
            .collect();
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
