import type { ReactNode } from "react";

export interface ProgressRingProps {
  /** Progress from 0 to 100; values outside the range are clamped. */
  value: number;
  /** Outer diameter in pixels. */
  size?: number;
  strokeWidth?: number;
  /** Accessible name of the progress indicator. */
  label: string;
  /** Text shown in the centre; defaults to the rounded percentage. */
  children?: ReactNode;
}

/** Circular progress indicator with an animated arc. */
export function ProgressRing({
  value,
  size = 96,
  strokeWidth = 10,
  label,
  children,
}: ProgressRingProps) {
  const clamped = Math.min(100, Math.max(0, value));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);
  return (
    <div
      className="rb-ring"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`} fill="none" strokeWidth={strokeWidth}>
          <circle className="rb-ring__track" cx={size / 2} cy={size / 2} r={radius} />
          <circle
            className="rb-ring__value"
            cx={size / 2}
            cy={size / 2}
            r={radius}
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
        </g>
      </svg>
      <span className="rb-ring__label">{children ?? `${Math.round(clamped)}%`}</span>
    </div>
  );
}
