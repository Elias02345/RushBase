import { ThemeProvider, ToastProvider } from "@rushbase/ui";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

function Toasts({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <ToastProvider closeLabel={t("common.close")} regionLabel={t("common.notifications")}>
      {children}
    </ToastProvider>
  );
}

/** Theme, data and notification providers shared by the app and the tests. */
export function AppProviders({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <ThemeProvider>
      <QueryClientProvider client={client}>
        <Toasts>{children}</Toasts>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
