import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import i18n from "./i18n";

// jsdom does not implement scrolling; the router calls it on navigation.
window.scrollTo = () => undefined;

afterEach(async () => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  await i18n.changeLanguage("de");
});
