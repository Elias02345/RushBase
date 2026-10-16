import { useEffect } from "react";

export interface ShortcutHandlers {
  togglePalette: () => void;
  openHelp: () => void;
  go: (target: "dashboard" | "library" | "processing" | "admin") => void;
  focusSearch: () => void;
}

const GO_KEYS: Record<string, "dashboard" | "library" | "processing" | "admin"> = {
  d: "dashboard",
  l: "library",
  p: "processing",
  a: "admin",
};

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * App-wide keyboard shortcuts: Ctrl/Cmd+K palette, `/` search, `?` help and `g` + letter to
 * jump between pages. Plain keys are ignored while typing or when a modal dialog is open.
 */
export function useGlobalShortcuts(handlers: ShortcutHandlers): void {
  const { togglePalette, openHelp, go, focusSearch } = handlers;

  useEffect(() => {
    let pendingGo = 0;

    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        togglePalette();
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
      if (isTyping(event.target)) return;
      if (document.querySelector("[role='dialog']")) return;

      if (event.key === "/") {
        event.preventDefault();
        focusSearch();
      } else if (event.key === "?") {
        event.preventDefault();
        openHelp();
      } else if (event.key === "g") {
        pendingGo = Date.now();
      } else if (pendingGo && Date.now() - pendingGo < 1200) {
        const target = GO_KEYS[event.key];
        pendingGo = 0;
        if (target) go(target);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [togglePalette, openHelp, go, focusSearch]);
}
