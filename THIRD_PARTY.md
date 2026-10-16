# Third-party software

Rushbase itself is licensed under Apache-2.0. This file lists direct dependencies and external
tools as of milestone M1; transitive dependencies are pinned in `uv.lock` and `pnpm-lock.yaml`.
Proprietary SDKs (for example Blackmagic RAW, RED) are never bundled.

## Python (server, worker, core, tooling)

| Package | Use | Licence |
| --- | --- | --- |
| FastAPI | HTTP API | MIT |
| Uvicorn | ASGI server | BSD-3-Clause |
| Pydantic, pydantic-settings | models, settings | MIT |
| SQLAlchemy | database toolkit | MIT |
| Alembic | migrations | MIT |
| psycopg 3 (+ binary) | PostgreSQL driver (bundles libpq) | LGPL-3.0-only |
| Typer | CLI | MIT |
| argon2-cffi | argon2id password hashing | MIT |
| blake3 (BLAKE3 Python bindings) | file fingerprints and hashes | MIT / Apache-2.0 |
| HTTPX | HTTP client | BSD-3-Clause |
| Hatchling | build backend | MIT |
| pytest, pytest-cov | tests | MIT |
| pytest-asyncio | async tests | Apache-2.0 |
| Ruff | lint/format | MIT |
| mypy | type checking | MIT |
| Pillow | test media (EXIF images) | MIT-CMU (HPND) |
| NumPy | test media | BSD-3-Clause |
| tifffile | test media (DNG-style TIFF) | BSD-3-Clause |
| pydantic-settings, platformdirs | worker configuration and data directories | MIT |
| defusedxml | safe parsing of camera sidecar XML | PSF-2.0 |
| pillow-heif (bundles libheif, libde265) | HEIC photos | BSD-3-Clause (libheif LGPL-3.0, libde265 LGPL-3.0) |
| rawpy (bundles LibRaw) | camera RAW/DNG previews | MIT (LibRaw: LGPL-2.1 / CDDL-1.0) |
| opencv-python-headless (OpenCV) | optical flow, image measures | Apache-2.0 (OpenCV 4.5+) |
| PySceneDetect 0.6.6 | shot detection | BSD-3-Clause |

## JavaScript / TypeScript (web, packages)

| Package | Use | Licence |
| --- | --- | --- |
| React, React DOM | UI | MIT |
| TanStack Router, TanStack Query | routing, data fetching | MIT |
| i18next, react-i18next | internationalisation | MIT |
| openapi-fetch, openapi-typescript | typed API client | MIT |
| Vite, @vitejs/plugin-react | build tooling | MIT |
| Vitest, jsdom | tests | MIT |
| Testing Library (react, jest-dom, user-event) | tests | MIT |
| TypeScript | compiler | Apache-2.0 |
| ESLint, typescript-eslint, eslint-plugin-react-hooks, eslint-plugin-react-refresh, globals | lint | MIT |
| Prettier | formatting | MIT |
| Radix UI primitives (dialog, tabs, select, checkbox, switch, tooltip, toast, visually-hidden) | accessible components | MIT |
| cmdk | command palette | MIT |
| lucide-react | icons | ISC |
| @tanstack/react-virtual | virtualised lists | MIT |
| @fontsource-variable/figtree, @fontsource-variable/jetbrains-mono | self-hosted fonts (Figtree, JetBrains Mono) | OFL-1.1 (font), MIT (package) |
| MSW (Mock Service Worker) | mock API for demos and tests | MIT |
| Playwright (@playwright/test), @axe-core/playwright | browser tests, accessibility checks | Apache-2.0, MPL-2.0 |

## External tools and images (not bundled in the repository)

| Software | Use | Licence |
| --- | --- | --- |
| PostgreSQL, pgvector (`pgvector/pgvector` image) | database | PostgreSQL License |
| `pg_dump` / `pg_restore` (PostgreSQL client tools) | scheduled backups and restore, invoked as separate processes | PostgreSQL License |
| FFmpeg / ffprobe | media probing, test media | LGPL-2.1+ / GPL-2.0+ (build dependent), invoked as a separate process |
| ExifTool 13.59 (pinned in `worker/binaries.lock.json`) | metadata extraction | Perl Artistic License / GPL, invoked as a separate process |
| Samba (`dockurr/samba` image) | simulated NAS in the dev stack only | GPL-3.0 (Samba), MIT (image scripts) |
| Docker Compose | dev orchestration | Apache-2.0 |
| uv | Python tooling | MIT / Apache-2.0 |
| pnpm | package manager | MIT |

Models (speech, vision, embeddings) arrive in milestone M4 and will be listed here with their
licences before they are enabled.
