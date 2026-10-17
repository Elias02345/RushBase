/** Facet keys that map to a single-valued URL filter. */
export const ENUM_KEYS = [
  "media_type",
  "status",
  "camera_model",
  "lens_model",
  "log_profile",
  "orientation",
  "shot_size",
  "camera_angle",
  "interior_exterior",
  "time_of_day",
  "person_identifiable",
  "review_state",
  "consent",
  "used_in_project",
] as const;
export type EnumKey = (typeof ENUM_KEYS)[number];

export function isEnumKey(key: string): key is EnumKey {
  return (ENUM_KEYS as readonly string[]).includes(key);
}

/** Taxonomy facets are keyed `tag.<field>` and carry `field:value` values. */
export function isTagKey(key: string): boolean {
  return key.startsWith("tag.");
}

const TRANSLATED: Record<string, string> = {
  media_type: "media",
  status: "assetStatus",
  orientation: "orientation",
  shot_size: "ai.shotSize",
  camera_angle: "ai.cameraAngle",
  interior_exterior: "ai.interiorExterior",
  time_of_day: "ai.timeOfDay",
  person_identifiable: "ai.tri",
  consent: "ai.tri",
  review_state: "ai.reviewState",
};

export function valueLabel(
  t: (key: string, options?: Record<string, unknown>) => string,
  key: string,
  value: string,
): string {
  if (isTagKey(key)) return value.slice(value.indexOf(":") + 1);
  const group = TRANSLATED[key];
  return group ? t(`${group}.${value}`, { defaultValue: value }) : value;
}
