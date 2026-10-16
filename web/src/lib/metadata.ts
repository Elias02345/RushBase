/** Presentation of raw technical metadata values (units, booleans, lists). */

type Translate = (key: string, options?: Record<string, unknown>) => string;

const NUMBER_FORMAT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

function mbit(bitsPerSecond: number): string {
  return `${NUMBER_FORMAT.format(bitsPerSecond / 1_000_000)} Mbit/s`;
}

function khz(hertz: number): string {
  return `${NUMBER_FORMAT.format(hertz / 1000)} kHz`;
}

/** Whether a value carries information (empty strings, lists and objects are hidden). */
export function hasValue(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.some(hasValue);
  if (typeof value === "object") return Object.values(value as object).some(hasValue);
  return true;
}

/** Format `value` of metadata field `key` for display; raw bits/s and Hz become Mbit/s and kHz. */
export function formatMetaValue(t: Translate, key: string, value: unknown): string {
  if (Array.isArray(value)) return value.filter(hasValue).map(String).join(", ");
  if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
  if (typeof value === "number") {
    switch (key) {
      case "bitrate":
        return mbit(value);
      case "bitrate_mbps":
        return `${NUMBER_FORMAT.format(value)} Mbit/s`;
      case "sample_rate":
        return khz(value);
      case "sample_rate_hz":
        return khz(value);
      case "shutter_s":
        return value > 0 && value < 1 ? `1/${Math.round(1 / value)} s` : `${value} s`;
      case "width":
      case "height":
        return `${value} px`;
      case "focal_length_mm":
      case "focal_length_35mm":
        return `${NUMBER_FORMAT.format(value)} mm`;
      case "focus_distance_m":
      case "alt_m":
      case "altitude_m":
        return `${NUMBER_FORMAT.format(value)} m`;
      case "white_balance_k":
        return `${value} K`;
      case "bit_depth":
        return `${value} bit`;
      case "fps":
      case "capture_fps":
        return `${NUMBER_FORMAT.format(value)} fps`;
      default:
        return NUMBER_FORMAT.format(value);
    }
  }
  return String(value);
}
