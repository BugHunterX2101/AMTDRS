import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// Two pages, deliberately separate bundles:
//
//   index.html      the marketing/landing page, served at /
//   app/index.html  the operator console, served at /app
//
// Splitting them is not cosmetic. The console is the live demo and must stay
// fast and unstyled-by-anything-else; the landing page carries fonts, motion
// and a much larger stylesheet. Separate entries mean neither page pays for
// the other's CSS, and the landing page's resets can never leak into the
// console's dense dark UI.
export default defineConfig({
  plugins: [react()],
  // Relative asset paths: the bundle is served by FastAPI from the same origin,
  // and app/index.html sits one directory deeper, so it needs ../assets/...
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        landing: resolve(__dirname, "index.html"),
        console: resolve(__dirname, "app/index.html"),
      },
    },
  },
  server: {
    // `npm run dev` runs on 5173 while the API runs on 8000. Proxying keeps the
    // browser on one origin so EventSource works without CORS preflight.
    proxy: {
      "/jobs": "http://127.0.0.1:8000",
      "/artifacts": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
      "/debug": "http://127.0.0.1:8000",
    },
  },
});
