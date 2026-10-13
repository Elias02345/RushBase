import type { HTMLAttributes } from "react";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}

/** Small status pill; the tone selects the colour pair. */
export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  const classes = ["rb-badge", tone === "neutral" ? "" : `rb-badge--${tone}`, className]
    .filter(Boolean)
    .join(" ");
  return <span className={classes} {...rest} />;
}
