import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { resetMockState } from "./api/mocks/handlers";
import { mockServer } from "./api/mocks/node";
import i18n from "./i18n";

// jsdom lacks these browser APIs that Radix, cmdk and the virtualiser rely on.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub;
Element.prototype.scrollIntoView ??= () => undefined;
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => undefined;
// jsdom does not implement scrolling; the router calls it on navigation.
window.scrollTo = () => undefined;

beforeAll(() => mockServer.listen({ onUnhandledFrame: "error" }));
afterAll(() => mockServer.close());

afterEach(async () => {
  cleanup();
  mockServer.resetHandlers();
  resetMockState();
  window.localStorage.clear();
  window.sessionStorage.clear();
  delete document.documentElement.dataset.theme;
  await i18n.changeLanguage("de");
});
