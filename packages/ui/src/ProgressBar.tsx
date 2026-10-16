import { cx } from "./cx";
import type { StatusTone } from "./StatusChip";

export interface ProgressBarProps {
  /** Progress from 0 to 100; values outside the range are clamped. `null` shows a busy bar. */
  value: number | null;
  /** Accessible name of the progress indicator. */
  label: string;
  tone?: StatusTone;
  className?: string;
}

/** Slim linear progress indicator (indeterminate when `value` is `null`). */
export function ProgressBar({ value, label, tone, className }: ProgressBarProps) {
  const clamped = value === null ? null : Math.min(100, Math.max(0, value));
  return (
    <div
      className={cx(
        "rb-bar",
        tone && `rb-bar--${tone}`,
        clamped === null && "rb-bar--busy",
        className,
      )}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped === null ? undefined : Math.round(clamped)}
    >
      <div
        className="rb-bar__value"
        style={clamped === null ? undefined : { width: `${clamped}%` }}
      />
    </div>
  );
}
