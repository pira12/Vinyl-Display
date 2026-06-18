import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API/websocket/art to the local backend so `npm run dev` works
// against `python -m backend.main`. In production the FastAPI server serves the
// built dist/ directly, so these proxies are dev-only.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
      "/art": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
