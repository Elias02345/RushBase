import "@fontsource-variable/figtree";
import "@fontsource-variable/jetbrains-mono";
import "@rushbase/ui/styles.css";
import "./styles/app.css";
import "./i18n";

import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createQueryClient } from "./api/queryClient";
import { AppProviders } from "./Providers";
import { createAppRouter } from "./router";

async function bootstrap() {
  // `VITE_MOCK=1` serves the API from in-browser mocks (demo and Playwright runs).
  if (import.meta.env.VITE_MOCK === "1") {
    const { startMockServer } = await import("./api/mocks/browser");
    await startMockServer();
  }

  const queryClient = createQueryClient(() => {
    if (!window.location.pathname.startsWith("/login")) {
      void router.navigate({ to: "/login", search: { redirect: window.location.pathname } });
    }
  });
  const router = createAppRouter(queryClient);

  const container = document.getElementById("root");
  if (!container) throw new Error("missing #root element");
  createRoot(container).render(
    <StrictMode>
      <AppProviders client={queryClient}>
        <RouterProvider router={router} />
      </AppProviders>
    </StrictMode>,
  );
}

void bootstrap();
