import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The backend base URL is configuration, never a hardcoded literal in app code.
 * `VITE_API_BASE_URL` lets a deployment point at a remote backend; in
 * development the default is the local proxy target below.
 */
const backendUrl = process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Requests go to the same origin and Vite forwards them, so the browser
      // never needs a CORS grant and the base URL stays in one place.
      "/api": {
        target: backendUrl,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
});
