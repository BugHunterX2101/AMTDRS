import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built bundle is served by FastAPI from the same origin at `/`, so relative
// asset paths are required — an absolute /assets/... breaks if the app is ever
// mounted under a prefix.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
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
