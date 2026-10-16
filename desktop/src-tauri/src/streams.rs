//! Long-running CLI children (`process`, `models pull`) streamed to the UI as events.

use crate::settings::Settings;
use crate::sidecar::{build, Logger};
use serde_json::json;
use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

/// Running streams by name (`process`, `models`) with their process ids.
#[derive(Default)]
pub struct Streams {
    running: Arc<Mutex<HashMap<String, u32>>>,
}

/// Validate the UI-supplied options of a stream and return the full CLI argument list.
pub fn stream_args(name: &str, options: &[String]) -> Result<Vec<String>, String> {
    let (prefix, flags_with_value, flags): (&[&str], &[&str], &[&str]) = match name {
        "process" => (
            &["process", "--json"],
            &["--stages", "--until", "--concurrency"],
            &["--exit-when-empty", "--auto-download"],
        ),
        "models" => (&["models", "pull", "--json"], &["--profile"], &["--all"]),
        _ => return Err(format!("unknown stream {name}")),
    };
    let mut args: Vec<String> = prefix.iter().map(|s| s.to_string()).collect();
    let mut iter = options.iter();
    while let Some(option) = iter.next() {
        if flags.contains(&option.as_str()) {
            args.push(option.clone());
        } else if flags_with_value.contains(&option.as_str()) {
            let value = iter.next().ok_or_else(|| format!("{option} needs a value"))?;
            if value.starts_with('-') || value.len() > 200 {
                return Err(format!("invalid value for {option}"));
            }
            args.push(option.clone());
            args.push(value.clone());
        } else if name == "models" && !option.starts_with('-') && option.len() < 100 {
            args.push(option.clone()); // a model id
        } else {
            return Err(format!("option not allowed: {option}"));
        }
    }
    Ok(args)
}

/// Ask a process to stop gracefully (SIGTERM; the worker releases its jobs).
pub fn terminate(pid: u32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as i32, libc::SIGTERM);
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill").args(["/PID", &pid.to_string(), "/T"]).output();
    }
}

fn kill(pid: u32) {
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as i32, libc::SIGKILL);
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"]).output();
    }
}

impl Streams {
    pub fn is_running(&self, name: &str) -> bool {
        self.running.lock().expect("streams lock").contains_key(name)
    }

    /// Start stream `name`; every stdout line becomes a `stream-event`, the end a `stream-exit`.
    pub fn start(
        &self,
        app: &AppHandle,
        settings: &Settings,
        log: &Arc<Logger>,
        name: &str,
        options: &[String],
    ) -> Result<u32, String> {
        let args = stream_args(name, options)?;
        if self.is_running(name) {
            return Err(format!("{name} is already running"));
        }
        log.line(&format!("stream {name} {}", args.join(" ")));
        let mut child = build(settings, &args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("cannot start the worker CLI: {e}"))?;
        let pid = child.id();
        self.running.lock().expect("streams lock").insert(name.to_string(), pid);
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr = child.stderr.take().expect("piped stderr");
        let (app_out, label) = (app.clone(), name.to_string());
        let running = Arc::clone(&self.running);
        let log_err = Arc::clone(log);
        let label_err = label.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                log_err.line(&format!("{label_err} stderr: {line}"));
            }
        });
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                let _ = app_out.emit("stream-event", json!({ "name": label, "line": line }));
            }
            let code = child.wait().ok().and_then(|s| s.code()).unwrap_or(-1);
            running.lock().expect("streams lock").remove(&label);
            let _ = app_out.emit("stream-exit", json!({ "name": label, "code": code }));
        });
        Ok(pid)
    }

    /// Gracefully stop one stream.
    pub fn stop(&self, name: &str) -> bool {
        let pid = self.running.lock().expect("streams lock").get(name).copied();
        pid.map(terminate).is_some()
    }

    /// Stop everything and wait up to `timeout` for the children to release their jobs.
    pub fn stop_all_blocking(&self, timeout: Duration) {
        let pids: Vec<u32> = self.running.lock().expect("streams lock").values().copied().collect();
        pids.iter().for_each(|pid| terminate(*pid));
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline && !self.running.lock().expect("streams lock").is_empty() {
            std::thread::sleep(Duration::from_millis(200));
        }
        for pid in self.running.lock().expect("streams lock").values() {
            kill(*pid);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opts(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn process_options_are_whitelisted() {
        let args = stream_args("process", &opts(&["--stages", "probe,preview", "--until", "06:30", "--exit-when-empty"])).unwrap();
        assert_eq!(args[..2], ["process", "--json"]);
        assert!(args.contains(&"probe,preview".to_string()));
        assert!(stream_args("process", &opts(&["--exec", "x"])).is_err());
        assert!(stream_args("process", &opts(&["--stages"])).is_err());
        assert!(stream_args("process", &opts(&["--stages", "--evil"])).is_err());
        assert!(stream_args("other", &[]).is_err());
    }

    #[test]
    fn models_pull_accepts_ids_and_profile() {
        let args = stream_args("models", &opts(&["--profile", "gpu8", "qwen3-vl-8b"])).unwrap();
        assert_eq!(args[..3], ["models", "pull", "--json"]);
        assert!(args.contains(&"qwen3-vl-8b".to_string()));
    }

    #[cfg(unix)]
    #[test]
    fn terminate_stops_a_child() {
        let mut child = std::process::Command::new("sleep").arg("30").spawn().unwrap();
        terminate(child.id());
        let status = child.wait().unwrap();
        assert!(!status.success());
    }
}
