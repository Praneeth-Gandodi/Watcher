import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { applyTheme, readInitialTheme } from "./theme";
import "./tokens.css";
import "./styles.css";

// Paint the stored or OS-preferred theme before React mounts, so the console
// never flashes the wrong surface on first load.
applyTheme(readInitialTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
