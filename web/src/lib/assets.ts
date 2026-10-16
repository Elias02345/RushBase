import type { SpriteSheet } from "@rushbase/ui";

import type { AssetSummary, MediaType } from "../api/contracts";
import { formatResolution } from "./format";

const MEDIA: readonly string[] = ["video", "photo", "audio", "other"];

/** The server sends the media type as a plain string; unknown values render as `other`. */
export function mediaTypeOf(asset: Pick<AssetSummary, "media_type">): MediaType {
  return MEDIA.includes(asset.media_type) ? (asset.media_type as MediaType) : "other";
}

/** The contact sheet is one row of `sprite_tiles` frames. */
export function spriteSheetOf(
  asset: Pick<AssetSummary, "sprite_url" | "sprite_tiles">,
): SpriteSheet | null {
  const count = asset.sprite_tiles ?? 0;
  return asset.sprite_url && count > 0
    ? { url: asset.sprite_url, cols: count, rows: 1, count }
    : null;
}

/** Small facts on a library tile, for example "4K" and "25 fps". */
export function tileFacts(asset: AssetSummary): string[] {
  const facts: string[] = [];
  const resolution = formatResolution(asset.width, asset.height);
  if (resolution) facts.push(resolution);
  if (asset.media_type === "video" && asset.fps) facts.push(`${Math.round(asset.fps)} fps`);
  return facts;
}
