import { useId, type HTMLAttributes, type ReactNode } from "react";

import { cx } from "./cx";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
  /** Right-aligned content in the header (a link, a button). */
  actions?: ReactNode;
  /** Remove inner padding (for tables and media that run edge to edge). */
  flush?: boolean;
}

/** Soft rounded surface. When a title is given the card is labelled by it. */
export function Card({ title, actions, flush, className, children, ...rest }: CardProps) {
  const titleId = useId();
  return (
    <section
      className={cx("rb-card", flush && "rb-card--flush", className)}
      aria-labelledby={title ? titleId : undefined}
      {...rest}
    >
      {title || actions ? (
        <header className="rb-card__header">
          {title ? (
            <h2 id={titleId} className="rb-card__title">
              {title}
            </h2>
          ) : null}
          {actions ? <div className="rb-card__actions">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}
