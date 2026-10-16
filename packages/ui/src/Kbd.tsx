import type { HTMLAttributes } from "react";

import { cx } from "./cx";

/** Keycap for keyboard shortcut hints. */
export function Kbd({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return <kbd className={cx("rb-kbd", className)} {...rest} />;
}
