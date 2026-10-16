import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { ThemeProvider, ToastProvider } from "@rushbase/ui";

import { App } from "./App";
import { createBridge } from "./bridge";
import { BridgeProvider } from "./bridge/context";
import { RunProvider } from "./state/run";

const bridge = createBridge();
const client = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 5_000 } },
});

/** Theme, toasts, host bridge, data cache and the run controller around the app. */
export function Providers() {
  const { t } = useTranslation();
  return (
    <ThemeProvider>
      <ToastProvider closeLabel={t("common.close")} regionLabel={t("common.notifications")}>
        <BridgeProvider bridge={bridge}>
          <QueryClientProvider client={client}>
            <RunProvider>
              <App />
            </RunProvider>
          </QueryClientProvider>
        </BridgeProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
