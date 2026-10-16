# ADR-0013: Web UI stack, styling and mock-first API development

- Status: Accepted
- Date: 2026-10-16

## Context
The web UI (milestone M3) must feel playful but calm for hours-long editing sessions, work offline
(self-hosted, no CDN), be fully keyboard accessible, and share its components with the Tauri companion
(M6). The server endpoints for search, facets and statistics are built in parallel, so the UI needs a
way to run and be tested before they exist.

## Decision
1. **Styling: plain CSS on design tokens, no Tailwind.** `packages/ui` ships `tokens.css` (OKLCH colours
   defined once with `light-dark()`, so light, dark and "follow the system" need no duplicated
   values) and `components.css` (BEM-style `rb-*` classes). Reasons: the same stylesheet works unchanged
   in the Tauri webview and any future host, tokens stay the single source of truth for status colours
   and motion, there is no build-time class scanning across package boundaries, and the CSS is small
   (about 14 kB gzip). App layout and pages live in `web/src/styles/app.css` on the same tokens.
2. **Behaviour: Radix UI primitives** (dialog, tabs, select, checkbox, switch, tooltip, toast) and
   **cmdk** for the command palette. They provide focus management, roving tabindex, typeahead and
   ARIA that would be costly and risky to rebuild. **@tanstack/react-virtual** virtualises the tile
   grid and `DataTable`; **lucide-react** provides the icon set.
3. **Typography: Figtree Variable** (UI) and **JetBrains Mono Variable** (paths, commands), both
   self-hosted through `@fontsource-variable/*`; nothing is fetched from a CDN.
4. **Colour language:** one accent (cobalt) for actions and selection, plus one hue per pipeline state
   (copying = sky, waiting = amber, processing = violet, filed = green, review = orange, quarantine =
   red). Colour is never the only signal; every chip carries text. Text pairs are chosen for WCAG AA
   and are checked by axe in the Playwright suite (light and dark).
5. **Motion:** short ease-out transitions, a springy (`linear()`) tile lift, a breathing dot for
   processing and flowing dots in the pipeline visual. Keyboard-initiated actions (palette) are not
   animated. Everything honours `prefers-reduced-motion`.
6. **Data layer:** TanStack Query on a typed `openapi-fetch` client. Endpoints already in OpenAPI use
   the generated client; the M3 read endpoints and a few proposed admin endpoints are described by
   hand in `web/src/api/contracts.ts` as an OpenAPI-shaped `paths` type. Swapping to generated types
   is a one-file change.
7. **Mock-first:** MSW answers the contract endpoints and keeps in-memory state (`VITE_MOCK=1`, demo and
   Playwright) or runs inside Vitest. The service worker and demo video are stripped from real builds.
8. **Routing:** TanStack Router with code-split page routes and typed, validated URL search params for
   library filters (invalid values are dropped, never thrown). Authentication is a route guard on the
   pathless `_app` layout; the guard sends first-run installs to `/setup`.
9. **Performance budget:** initial JavaScript below 300 kB gzip (currently about 170 kB including the
   shell), pages are lazy chunks, images are lazy with reserved aspect-ratio boxes.

## Consequences
- Components in `packages/ui` are framework-light and reusable by the desktop app.
- Facets are single-valued per key because the list contract takes one value per filter parameter.
- Until the server publishes the contract endpoints, a real server answers 404 for them; the mock
  layer is the reference for the expected shapes (see `docs/STATUS.md` for the list).
