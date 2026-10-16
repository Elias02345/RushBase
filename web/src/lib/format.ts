import i18n from "../i18n";

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

function locale(): string {
  return i18n.language === "en" ? "en-GB" : "de-DE";
}

/** `1536` -> `1.5 KB` (decimal units, as file managers on NAS shares show them). */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const exponent = Math.min(UNITS.length - 1, Math.floor(Math.log10(bytes) / 3));
  const value = bytes / 1000 ** exponent;
  const digits = value >= 100 || exponent === 0 ? 0 : value >= 10 ? 1 : 2;
  return `${new Intl.NumberFormat(locale(), { maximumFractionDigits: digits }).format(value)} ${UNITS[exponent]}`;
}

/** `83250` -> `1:23`; `3_725_000` -> `1:02:05`. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "";
  const total = Math.max(0, Math.round(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

/** Long media durations in words, for backlog estimates: `3 h 12 min`. */
export function formatDurationWords(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 1) return "< 1 min";
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours} h ${minutes % 60} min` : `${minutes} min`;
}

/** Resolution label: `4K`, `1080p`, or `WxH`. */
export function formatResolution(width: number | null, height: number | null): string | null {
  if (!width || !height) return null;
  const long = Math.max(width, height);
  const short = Math.min(width, height);
  if (long >= 7600) return "8K";
  if (long >= 3800) return "4K";
  if (long >= 2500) return "2.7K";
  if (short >= 1080 && long >= 1900) return "1080p";
  if (short >= 720) return "720p";
  return `${width}×${height}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Intl.DateTimeFormat(locale(), { dateStyle: "medium" }).format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(iso),
  );
}

const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
  ["second", 1],
];

/** `2 hours ago` style text; `now` is injectable for tests. */
export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "–";
  const seconds = Math.round((Date.parse(iso) - now) / 1000);
  const formatter = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  for (const [unit, size] of STEPS) {
    if (Math.abs(seconds) >= size || unit === "second") {
      return formatter.format(Math.round(seconds / size), unit);
    }
  }
  return "–";
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(locale()).format(value);
}
