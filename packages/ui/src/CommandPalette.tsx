import { Command } from "cmdk";
import type { ReactNode } from "react";

import { Kbd } from "./Kbd";

export interface CommandItem {
  id: string;
  label: string;
  icon?: ReactNode;
  /** Extra search terms. */
  keywords?: string[];
  /** Shortcut hint, for example "G then L". */
  shortcut?: string;
  onSelect: () => void;
}

export interface CommandGroup {
  heading: string;
  items: CommandItem[];
}

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  groups: CommandGroup[];
  label: string;
  placeholder: string;
  emptyLabel: string;
}

/** Command palette (Ctrl/Cmd+K). Opens and closes instantly: it is used hundreds of times a day. */
export function CommandPalette({
  open,
  onOpenChange,
  groups,
  label,
  placeholder,
  emptyLabel,
}: CommandPaletteProps) {
  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label={label}
      overlayClassName="rb-overlay rb-overlay--instant"
      contentClassName="rb-palette"
    >
      <Command.Input className="rb-palette__input" placeholder={placeholder} />
      <Command.List className="rb-palette__list">
        <Command.Empty className="rb-palette__empty">{emptyLabel}</Command.Empty>
        {groups.map((group) => (
          <Command.Group key={group.heading} heading={group.heading} className="rb-palette__group">
            {group.items.map((item) => (
              <Command.Item
                key={item.id}
                value={`${item.label} ${item.keywords?.join(" ") ?? ""}`}
                onSelect={() => {
                  onOpenChange(false);
                  item.onSelect();
                }}
                className="rb-palette__item"
              >
                <span className="rb-palette__icon" aria-hidden="true">
                  {item.icon}
                </span>
                <span className="rb-palette__label">{item.label}</span>
                {item.shortcut ? <Kbd>{item.shortcut}</Kbd> : null}
              </Command.Item>
            ))}
          </Command.Group>
        ))}
      </Command.List>
    </Command.Dialog>
  );
}
