import * as RadixDialog from "@radix-ui/react-dialog";
import * as RadixTabs from "@radix-ui/react-tabs";
import { X } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

import { cx } from "./cx";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  /** Buttons aligned to the bottom right. */
  footer?: ReactNode;
  closeLabel: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

function Sheet({
  variant,
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  closeLabel,
  size = "md",
  className,
}: DialogProps & { variant: "dialog" | "drawer" }) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="rb-overlay" />
        <RadixDialog.Content
          className={cx(`rb-${variant}`, `rb-${variant}--${size}`, className)}
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <header className="rb-sheet__header">
            <div>
              <RadixDialog.Title className="rb-sheet__title">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="rb-sheet__description">
                  {description}
                </RadixDialog.Description>
              ) : null}
            </div>
            <RadixDialog.Close
              className="rb-iconbutton rb-iconbutton--ghost"
              aria-label={closeLabel}
            >
              <X size={18} aria-hidden="true" />
            </RadixDialog.Close>
          </header>
          <div className="rb-sheet__body">{children}</div>
          {footer ? <footer className="rb-sheet__footer">{footer}</footer> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** Centred modal dialog. Focus is trapped, Escape closes, focus returns to the trigger. */
export function Dialog(props: DialogProps) {
  return <Sheet variant="dialog" {...props} />;
}

/** Side sheet sliding in from the right; same accessibility behaviour as `Dialog`. */
export function Drawer(props: DialogProps) {
  return <Sheet variant="drawer" {...props} />;
}

/** Tabs on Radix: arrow keys move between triggers, panels are labelled by their trigger. */
export const Tabs = RadixTabs.Root;

export function TabsList({ className, ...rest }: ComponentPropsWithoutRef<typeof RadixTabs.List>) {
  return <RadixTabs.List className={cx("rb-tabs", className)} {...rest} />;
}

export function TabsTrigger({
  className,
  ...rest
}: ComponentPropsWithoutRef<typeof RadixTabs.Trigger>) {
  return <RadixTabs.Trigger className={cx("rb-tab", className)} {...rest} />;
}

export function TabsContent({
  className,
  ...rest
}: ComponentPropsWithoutRef<typeof RadixTabs.Content>) {
  return <RadixTabs.Content className={cx("rb-tabpanel", className)} {...rest} />;
}
