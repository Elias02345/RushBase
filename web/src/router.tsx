import type { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
} from "@tanstack/react-router";

import { ApiError } from "./api/client";
import { fetchHealth, fetchMe, hasRole, keys } from "./api/queries";
import { ErrorPage, NotFoundPage } from "./shell/ErrorPages";
import { AppShell } from "./shell/AppShell";
import {
  validateAdminSearch,
  validateAssetSearch,
  validateLibrarySearch,
  validateLoginSearch,
  validateNeedsSearch,
  validateProcessingSearch,
} from "./routes/search";

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: NotFoundPage,
  errorComponent: ErrorPage,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: validateLoginSearch,
  component: lazyRouteComponent(() => import("./pages/LoginPage"), "LoginPage"),
});

const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/setup",
  component: lazyRouteComponent(() => import("./pages/SetupPage"), "SetupPage"),
});

/** Pathless layout: everything below requires a session and renders inside the app shell. */
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_app",
  component: AppShell,
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.ensureQueryData({
        queryKey: keys.me,
        queryFn: fetchMe,
        staleTime: 60_000,
      });
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
      const health = await fetchHealth().catch(() => null);
      if (health?.setup_required) throw redirect({ to: "/setup" });
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
  },
});

const dashboardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  component: lazyRouteComponent(() => import("./pages/DashboardPage"), "DashboardPage"),
});

const libraryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/library",
  validateSearch: validateLibrarySearch,
  component: lazyRouteComponent(() => import("./pages/LibraryPage"), "LibraryPage"),
});

const assetRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/assets/$id",
  validateSearch: validateAssetSearch,
  component: lazyRouteComponent(() => import("./pages/AssetPage"), "AssetPage"),
});

const processingRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/processing",
  validateSearch: validateProcessingSearch,
  component: lazyRouteComponent(() => import("./pages/ProcessingPage"), "ProcessingPage"),
});

const needsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/needs",
  validateSearch: validateNeedsSearch,
  component: lazyRouteComponent(() => import("./pages/NeedsPage"), "NeedsPage"),
});

const adminRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/admin",
  validateSearch: validateAdminSearch,
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData({ queryKey: keys.me, queryFn: fetchMe });
    if (!hasRole(me.role, "admin")) throw redirect({ to: "/" });
  },
  component: lazyRouteComponent(() => import("./pages/AdminPage"), "AdminPage"),
});

const reviewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/review",
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData({ queryKey: keys.me, queryFn: fetchMe });
    if (!hasRole(me.role, "editor")) throw redirect({ to: "/" });
  },
  component: lazyRouteComponent(() => import("./pages/ReviewPage"), "ReviewPage"),
});

const privacyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/privacy",
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData({ queryKey: keys.me, queryFn: fetchMe });
    if (!hasRole(me.role, "editor")) throw redirect({ to: "/" });
  },
  component: lazyRouteComponent(() => import("./pages/PrivacyPage"), "PrivacyPage"),
});

const goldenRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/golden",
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData({ queryKey: keys.me, queryFn: fetchMe });
    if (!hasRole(me.role, "editor")) throw redirect({ to: "/" });
  },
  component: lazyRouteComponent(() => import("./pages/GoldenPage"), "GoldenPage"),
});

const taxonomyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/admin/taxonomy",
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData({ queryKey: keys.me, queryFn: fetchMe });
    if (!hasRole(me.role, "editor")) throw redirect({ to: "/" });
  },
  component: lazyRouteComponent(() => import("./pages/TaxonomyPage"), "TaxonomyPage"),
});

export const routeTree = rootRoute.addChildren([
  loginRoute,
  setupRoute,
  appRoute.addChildren([
    dashboardRoute,
    libraryRoute,
    assetRoute,
    reviewRoute,
    goldenRoute,
    privacyRoute,
    processingRoute,
    needsRoute,
    adminRoute,
    taxonomyRoute,
  ]),
]);

export function createAppRouter(
  queryClient: QueryClient,
  history?: Parameters<typeof createRouter>[0]["history"],
) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    scrollRestoration: true,
    history,
  });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
