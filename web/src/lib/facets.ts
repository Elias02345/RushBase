/** Facet keys that map to a single-valued URL filter. */
export const ENUM_KEYS = [
  "media_type",
  "status",
  "camera_model",
  "lens_model",
  "log_profile",
  "orientation",
] as const;
export type EnumKey = (typeof ENUM_KEYS)[number];

export function isEnumKey(key: string): key is EnumKey {
  return (ENUM_KEYS as readonly string[]).includes(key);
}

const TRANSLATED: Record<string, string> = {
  media_type: "media",
  status: "assetStatus",
  orientation: "orientation",
};

export function valueLabel(
  t: (key: string, options?: Record<string, unknown>) => string,
  key: string,
  value: string,
): string {
  const group = TRANSLATED[key];
  return group ? t(`${group}.${value}`, { defaultValue: value }) : value;
}
