import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cx } from "./cx";
import { Tooltip } from "./Tooltip";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Accessible name; also shown as a tooltip. */
  label: string;
  icon: ReactNode;
  variant?: "ghost" | "secondary" | "primary";
  size?: "md" | "sm";
  /** Pressed state for toggle buttons. */
  pressed?: boolean;
  /** Hide the tooltip (for example when a visible shortcut hint says the same). */
  noTooltip?: boolean;
}

/** Square icon-only button with a required accessible name. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  {
    label,
    icon,
    variant = "ghost",
    size = "md",
    pressed,
    noTooltip,
    className,
    type = "button",
    ...rest
  },
  ref,
) {
  const button = (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      className={cx(
        "rb-iconbutton",
        `rb-iconbutton--${variant}`,
        size === "sm" && "rb-iconbutton--sm",
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  );
  return noTooltip ? button : <Tooltip content={label}>{button}</Tooltip>;
});
