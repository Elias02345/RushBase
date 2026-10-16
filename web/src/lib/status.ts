import type { StatusTone } from "@rushbase/ui";

/** Asset lifecycle status -> chip colour. */
export function assetTone(status: string): StatusTone {
  switch (status) {
    case "ingesting":
      return "copying";
    case "processing":
      return "processing";
    case "review":
      return "review";
    case "filed":
      return "filed";
    case "quarantined":
      return "quarantine";
    default:
      return "neutral";
  }
}

/** Batch status -> chip colour (paused batches wait). */
export function batchTone(status: string, paused: boolean): StatusTone {
  if (paused) return "waiting";
  switch (status) {
    case "scanning":
      return "copying";
    case "waiting":
      return "waiting";
    case "processing":
      return "processing";
    case "done":
      return "filed";
    case "error":
      return "quarantine";
    default:
      return "neutral";
  }
}
