/** Short human duration such as `2 h 14 min` or `45 s` (same in German and English). */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 90) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** Clip duration `m:ss` or `h:mm:ss`. */
export function formatClock(milliseconds: number | null): string {
  if (milliseconds == null) return "";
  const total = Math.round(milliseconds / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

/** Locale aware number with at most `digits` decimals. */
export function formatNumber(value: number, language: string, digits = 1): string {
  return new Intl.NumberFormat(language, { maximumFractionDigits: digits }).format(value);
}

/** Bytes as MB/GB. */
export function formatBytes(bytes: number, language: string): string {
  if (bytes >= 1e9) return `${formatNumber(bytes / 1e9, language)} GB`;
  return `${formatNumber(bytes / 1e6, language, 0)} MB`;
}

/** Clock time `HH:MM` of a moment `seconds` from now. */
export function clockIn(seconds: number, now = Date.now()): string {
  const at = new Date(now + seconds * 1000);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

/** Accept either a bare token or a pasted `rushbase enroll --server U --token T [--name N]` command. */
export function parseEnrollCommand(input: string): {
  server?: string;
  token?: string;
  name?: string;
} {
  const read = (flag: string): string | undefined => {
    const match = input.match(new RegExp(`--${flag}(?:=|\\s+)("[^"]*"|'[^']*'|\\S+)`));
    return match?.[1]?.replace(/^["']|["']$/g, "");
  };
  if (!/--(server|token)/.test(input)) return { token: input.trim() || undefined };
  return { server: read("server"), token: read("token"), name: read("name") };
}
