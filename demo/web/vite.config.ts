import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, Vite serves the app and forwards /api to demo/server.py.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8787" } },
});
