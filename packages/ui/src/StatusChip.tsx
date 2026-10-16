import type { HTMLAttributes, ReactNode } from "react";

import { cx } from "./cx";

/** Pipeline states, each with its own hue (see tokens.css). */
export type StatusTone =
  "copying" | "waiting" | "processing" | "filed" | "review" | "quarantine" | "neutral";

export interface StatusChipProps extends HTMLAttributes<HTMLSpanElement> {
  tone: StatusTone;
  children: ReactNode;
  /** Hide the leading dot (for dense tables). */
  plain?: boolean;
}

/**
 * Compact status label. Colour is never the only signal: the text always names the state.
 * The processing dot breathes gently (disabled for reduced motion).
 */
export function StatusChip({ tone, children, plain, className, ...rest }: StatusChipProps) {
  return (
    <span className={cx("rb-chip", `rb-chip--${tone}`, className)} {...rest}>
      {plain ? null : <span className="rb-chip__dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
