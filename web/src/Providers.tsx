import { ThemeProvider, ToastProvider } from "@rushbase/ui";
import { QueryClient, QueryClientProvider, QueryCache, MutationCache } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "./api/client";

/** Create the query client; `onUnauthorized` runs when the session expired mid-use. */
export function createQueryClient(onUnauthorized?: () => void): QueryClient {
  const handle = (error: unknown) => {
    if (error instanceof ApiError && error.status === 401) onUnauthorized?.();
  };
  return new QueryClient({
    // The session probe (`me`) is handled by the route guard, which also knows about setup.
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (query.queryKey[0] !== "me") handle(error);
      },
    }),
    // A wrong password on the login form is a 401 too and must stay on that form.
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.options.mutationKey?.[0] !== "login") handle(error);
      },
    }),
    defaultOptions: {
      queries: {
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
        staleTime: 10_000,
      },
    },
  });
}

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
