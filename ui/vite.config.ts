import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies API calls to the running stack on 127.0.0.1:8080.
// DBK_API_URL overrides the target, e.g. http://host.docker.internal:8080 when vite runs in a container.
const api = process.env.DBK_API_URL || "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": api, "/health": api },
  },
  build: {
    chunkSizeWarningLimit: 800,
  },
});
