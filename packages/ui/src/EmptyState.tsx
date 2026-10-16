import type { ReactNode } from "react";

import { cx } from "./cx";

export interface EmptyStateProps {
  /** Decorative icon (rendered inside a soft tile). */
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  /** Primary next step. */
  action?: ReactNode;
  className?: string;
  /** Smaller layout for use inside cards. */
  compact?: boolean;
}

/** Friendly explanation of an empty area that teaches what to do next. */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
  compact,
}: EmptyStateProps) {
  return (
    <div className={cx("rb-empty", compact && "rb-empty--compact", className)}>
      {icon ? (
        <div className="rb-empty__icon" aria-hidden="true">
          {icon}
        </div>
      ) : null}
      <h3 className="rb-empty__title">{title}</h3>
      {description ? <p className="rb-empty__text">{description}</p> : null}
      {action ? <div className="rb-empty__action">{action}</div> : null}
    </div>
  );
}
