import type { HTMLAttributes } from "react";

import { cx } from "./cx";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}

/** Small neutral label for counts and metadata (use `StatusChip` for pipeline states). */
export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  return <span className={cx("rb-badge", `rb-badge--${tone}`, className)} {...rest} />;
}
