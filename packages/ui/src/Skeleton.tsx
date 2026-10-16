import type { CSSProperties, HTMLAttributes } from "react";

import { cx } from "./cx";

export interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  width?: CSSProperties["width"];
  height?: CSSProperties["height"];
  radius?: CSSProperties["borderRadius"];
}

/** Shimmering placeholder that holds the layout while data loads. */
export function Skeleton({
  width,
  height = "1em",
  radius,
  className,
  style,
  ...rest
}: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cx("rb-skeleton", className)}
      style={{ width, height, borderRadius: radius, ...style }}
      {...rest}
    />
  );
}
