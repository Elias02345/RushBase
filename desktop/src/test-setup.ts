import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

import i18n from "./i18n";

configure({ asyncUtilTimeout: 6_000 });

// jsdom lacks these browser APIs that Radix and the tiles rely on.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub;
Element.prototype.scrollIntoView ??= () => undefined;
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => undefined;
window.scrollTo = () => undefined;

afterEach(async () => {
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
  await i18n.changeLanguage("de");
});
