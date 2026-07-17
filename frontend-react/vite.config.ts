import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server fronts the Python backend (uvicorn on :8000) so the app is
// same-origin in dev exactly as it will be when FastAPI serves the built
// bundle — no CORS, and ws:// URLs derive from location.host.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/login": "http://localhost:8000",
      "/register": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
