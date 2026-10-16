import { Loader2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cx } from "./cx";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "md" | "sm" | "lg";
  /** Shows a spinner and disables the button while an action runs. */
  loading?: boolean;
  /** Icon rendered before the label. */
  icon?: ReactNode;
}

/** Pill-shaped button. Defaults to `type="button"` so it never submits forms by accident. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    className,
    type = "button",
    loading = false,
    icon,
    disabled,
    children,
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        "rb-button",
        `rb-button--${variant}`,
        size !== "md" && `rb-button--${size}`,
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Loader2 className="rb-spin" size={16} aria-hidden="true" /> : icon}
      {children}
    </button>
  );
});
