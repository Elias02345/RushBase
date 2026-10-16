import "@fontsource-variable/figtree";
import "@fontsource-variable/jetbrains-mono";
import "@rushbase/ui/styles.css";
import "./styles.css";
import "./i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Providers } from "./Providers";

const container = document.getElementById("root");
if (!container) throw new Error("missing #root element");
createRoot(container).render(
  <StrictMode>
    <Providers />
  </StrictMode>,
);
