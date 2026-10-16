//! Platform probes: GPU telemetry, user idle time, drag-out eligibility and environment facts.

use serde::Serialize;
use std::process::Command;

/// One `nvidia-smi` sample (all fields optional: other vendors report nothing).
#[derive(Serialize, Debug, PartialEq, Default)]
pub struct GpuStats {
    pub name: String,
    pub temperature_c: Option<f64>,
    pub power_w: Option<f64>,
    pub power_limit_w: Option<f64>,
    pub utilization_pct: Option<f64>,
    pub memory_used_mb: Option<f64>,
    pub memory_total_mb: Option<f64>,
}

/// Parse `nvidia-smi --format=csv,noheader,nounits` for the fields requested in [`gpu_stats`].
pub fn parse_nvidia_smi(text: &str) -> Option<GpuStats> {
    let line = text.lines().find(|l| !l.trim().is_empty())?;
    let cells: Vec<&str> = line.split(',').map(str::trim).collect();
    if cells.len() < 7 {
        return None;
    }
    let number = |i: usize| cells[i].parse::<f64>().ok();
    Some(GpuStats {
        name: cells[0].to_string(),
        temperature_c: number(1),
        power_w: number(2),
        power_limit_w: number(3),
        utilization_pct: number(4),
        memory_used_mb: number(5),
        memory_total_mb: number(6),
    })
}

/// Telemetry of the first NVIDIA GPU, or `None` when `nvidia-smi` is unavailable.
pub fn gpu_stats() -> Option<GpuStats> {
    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,temperature.gpu,power.draw,power.limit,utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ])
        .output()
        .ok()?;
    output.status.success().then(|| parse_nvidia_smi(&String::from_utf8_lossy(&output.stdout))).flatten()
}

/// Seconds since the last keyboard/mouse input, where the platform can tell.
pub fn idle_seconds() -> Option<u64> {
    #[cfg(target_os = "linux")]
    {
        if let Ok(out) = Command::new("xprintidle").output() {
            if out.status.success() {
                return String::from_utf8_lossy(&out.stdout).trim().parse::<u64>().ok().map(|ms| ms / 1000);
            }
        }
        return linux_logind_idle();
    }
    #[cfg(target_os = "macos")]
    {
        let out = Command::new("ioreg").args(["-c", "IOHIDSystem"]).output().ok()?;
        let text = String::from_utf8_lossy(&out.stdout);
        let line = text.lines().find(|l| l.contains("HIDIdleTime"))?;
        let nanos: u64 = line.split('=').nth(1)?.trim().parse().ok()?;
        return Some(nanos / 1_000_000_000);
    }
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::SystemInformation::GetTickCount;
        use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
        let mut info = LASTINPUTINFO { cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32, dwTime: 0 };
        // SAFETY: `info` is a valid, correctly sized LASTINPUTINFO.
        if unsafe { GetLastInputInfo(&mut info) } == 0 {
            return None;
        }
        return Some(u64::from(unsafe { GetTickCount() }.wrapping_sub(info.dwTime)) / 1000);
    }
    #[allow(unreachable_code)]
    None
}

#[cfg(target_os = "linux")]
fn linux_logind_idle() -> Option<u64> {
    let session = std::env::var("XDG_SESSION_ID").ok()?;
    let out = Command::new("loginctl")
        .args(["show-session", &session, "-p", "IdleHint", "-p", "IdleSinceHint", "--value"])
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let mut lines = text.lines();
    let hint = lines.next()?.trim();
    let since: u64 = lines.next()?.trim().parse().ok()?;
    if hint != "yes" || since == 0 {
        return Some(0);
    }
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).ok()?.as_micros() as u64;
    Some(now.saturating_sub(since) / 1_000_000)
}

/// Why drag-out is (not) offered for a path.
#[derive(Serialize, Debug, PartialEq)]
pub struct DragCheck {
    pub ok: bool,
    pub reason: Option<String>,
}

/// File-system types that must not be dragged directly (ADR-0004).
const NETWORK_FS: &[&str] = &["cifs", "smb3", "smbfs", "nfs", "nfs4", "9p", "fuse.sshfs", "davfs", "afpfs"];

pub fn is_wayland() -> bool {
    std::env::var("XDG_SESSION_TYPE").is_ok_and(|v| v == "wayland") || std::env::var("WAYLAND_DISPLAY").is_ok()
}

/// Decide from the path and the environment whether drag-out may be attempted.
pub fn drag_check(path: &str, mounts: Option<&str>) -> DragCheck {
    let deny = |reason: &str| DragCheck { ok: false, reason: Some(reason.to_string()) };
    if cfg!(target_os = "linux") && is_wayland() {
        return deny("wayland");
    }
    if path.chars().count() > 240 {
        return deny("path_too_long");
    }
    if path.starts_with("\\\\") {
        return deny("network_mount");
    }
    if let Some(table) = mounts {
        let best = table
            .lines()
            .filter_map(|line| {
                let mut parts = line.split_whitespace();
                let (_dev, mount, fs) = (parts.next()?, parts.next()?, parts.next()?);
                let mount = mount.replace("\\040", " ");
                let inside = path == mount || path.starts_with(&format!("{}/", mount.trim_end_matches('/')));
                inside.then_some((mount.len(), fs.to_string()))
            })
            .max_by_key(|(len, _)| *len);
        if best.is_some_and(|(_, fs)| NETWORK_FS.contains(&fs.as_str())) {
            return deny("network_mount");
        }
    }
    DragCheck { ok: true, reason: None }
}

/// Facts for the UI about the environment.
#[derive(Serialize)]
pub struct PlatformInfo {
    pub os: &'static str,
    pub wayland: bool,
    pub appimage: bool,
}

pub fn platform_info() -> PlatformInfo {
    PlatformInfo {
        os: std::env::consts::OS,
        wayland: cfg!(target_os = "linux") && is_wayland(),
        appimage: std::env::var("APPIMAGE").is_ok(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nvidia_smi_output() {
        let stats = parse_nvidia_smi("NVIDIA GeForce RTX 3070, 61, 123.45, 220.00, 97, 7100, 8192\n").unwrap();
        assert_eq!(stats.name, "NVIDIA GeForce RTX 3070");
        assert_eq!(stats.temperature_c, Some(61.0));
        assert_eq!(stats.power_limit_w, Some(220.0));
        assert_eq!(stats.memory_total_mb, Some(8192.0));
    }

    #[test]
    fn unsupported_fields_become_none() {
        let stats = parse_nvidia_smi("GPU, 50, [N/A], [N/A], 3, 10, 20").unwrap();
        assert_eq!(stats.power_w, None);
        assert!(parse_nvidia_smi("").is_none());
        assert!(parse_nvidia_smi("only,three,cells").is_none());
    }

    #[test]
    fn network_mounts_and_long_paths_are_refused() {
        if cfg!(target_os = "linux") && is_wayland() {
            return;
        }
        let table = "/dev/sda1 / ext4 rw 0 0\n//nas/media /mnt/nas cifs rw 0 0\n";
        assert!(drag_check("/home/me/clip.mov", Some(table)).ok);
        assert_eq!(drag_check("/mnt/nas/Content/a.mov", Some(table)).reason.as_deref(), Some("network_mount"));
        assert_eq!(drag_check("/mnt/nasty/a.mov", Some(table)).reason, None);
        assert_eq!(drag_check(&format!("/home/{}", "x".repeat(250)), None).reason.as_deref(), Some("path_too_long"));
        assert_eq!(drag_check("\\\\nas\\share\\a.mov", None).reason.as_deref(), Some("network_mount"));
    }
}
