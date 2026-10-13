import type { HTMLAttributes, ReactNode } from "react";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title?: ReactNode;
}

/** Rounded surface that groups related content, with an optional heading. */
export function Card({ title, className, children, ...rest }: CardProps) {
  const classes = ["rb-card", className].filter(Boolean).join(" ");
  return (
    <section className={classes} {...rest}>
      {title ? <h2 className="rb-card__title">{title}</h2> : null}
      {children}
    </section>
  );
}
