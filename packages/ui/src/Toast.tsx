import * as RadixToast from "@radix-ui/react-toast";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { cx } from "./cx";

export interface ToastOptions {
  title: string;
  description?: string;
  tone?: "success" | "error" | "info";
}

interface ToastItem extends ToastOptions {
  id: number;
}

interface ToastContextValue {
  push: (toast: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);
const ICONS = { success: CheckCircle2, error: AlertTriangle, info: Info } as const;

/** Mount once near the root; call `useToast().push(...)` anywhere below. */
export function ToastProvider({
  children,
  closeLabel,
  regionLabel,
}: {
  children: ReactNode;
  closeLabel: string;
  regionLabel: string;
}) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((toast: ToastOptions) => {
    setItems((current) => [...current, { ...toast, id: Date.now() + Math.random() }]);
  }, []);
  const value = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={value}>
      <RadixToast.Provider swipeDirection="right" duration={5000} label={regionLabel}>
        {children}
        {items.map((item) => {
          const tone = item.tone ?? "info";
          const Icon = ICONS[tone];
          return (
            <RadixToast.Root
              key={item.id}
              className={cx("rb-toast", `rb-toast--${tone}`)}
              onOpenChange={(open) => {
                if (!open) setItems((current) => current.filter((t) => t.id !== item.id));
              }}
            >
              <Icon className="rb-toast__icon" size={20} aria-hidden="true" />
              <div className="rb-toast__text">
                <RadixToast.Title className="rb-toast__title">{item.title}</RadixToast.Title>
                {item.description ? (
                  <RadixToast.Description className="rb-toast__description">
                    {item.description}
                  </RadixToast.Description>
                ) : null}
              </div>
              <RadixToast.Close
                className="rb-iconbutton rb-iconbutton--ghost rb-iconbutton--sm"
                aria-label={closeLabel}
              >
                <X size={14} aria-hidden="true" />
              </RadixToast.Close>
            </RadixToast.Root>
          );
        })}
        <RadixToast.Viewport className="rb-toast-viewport" />
      </RadixToast.Provider>
    </ToastContext.Provider>
  );
}

/** Access the toast queue; must be used below a `ToastProvider`. */
// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used within a ToastProvider");
  return context;
}
