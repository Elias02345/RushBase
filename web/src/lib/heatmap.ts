import type { HeatDimension } from "../api/contracts";
import type { LibrarySearch } from "../routes/search";

/** Library filter that selects `value` of the dimension (taxonomy fields use `tag`). */
export function filterFor(
  base: LibrarySearch,
  dimension: Pick<HeatDimension, "key" | "kind">,
  value: string,
): LibrarySearch {
  if (dimension.kind === "taxonomy") {
    return { ...base, tag: [...(base.tag ?? []), `${dimension.key}:${value}`] };
  }
  return { ...base, [dimension.key]: value } as LibrarySearch;
}
