import type { TagInfo } from "../api/contracts";

/** `m:ss` / `h:mm:ss` timecode of a millisecond position. */
export function timecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
}

export interface TagGroup {
  key: string;
  labelDe: string;
  labelEn: string;
  tags: TagInfo[];
}

/** Tags grouped by taxonomy field in the order the server sent them. */
export function groupTags(tags: readonly TagInfo[]): TagGroup[] {
  const groups = new Map<string, TagGroup>();
  for (const tag of tags) {
    const group = groups.get(tag.field_key) ?? {
      key: tag.field_key,
      labelDe: tag.field_label_de,
      labelEn: tag.field_label_en,
      tags: [],
    };
    group.tags.push(tag);
    groups.set(tag.field_key, group);
  }
  return [...groups.values()];
}

/** The value shown for a tag in the given UI language (falls back to the canonical value). */
export function tagLabel(tag: Pick<TagInfo, "value" | "label_de" | "label_en">, lang: string) {
  return (lang === "en" ? tag.label_en : tag.label_de) ?? tag.value;
}

export const SOURCE_TONE: Record<string, "success" | "info" | "neutral"> = {
  human: "success",
  claude: "info",
  ai: "neutral",
};

/** Confidence as a whole percentage for badges. */
export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : `${Math.round(value * 100)} %`;
}
