import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyStoredTheme } from "./state/useTheme";
import "./styles/global.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("root container missing from index.html");
}

// Theme first, so the first painted frame is already correct.
applyStoredTheme();

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
