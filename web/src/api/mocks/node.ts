import { setupServer } from "msw/node";

import { handlers } from "./handlers";

/** Mock server for Vitest; the test setup starts, resets and stops it. */
export const mockServer = setupServer(...handlers);
