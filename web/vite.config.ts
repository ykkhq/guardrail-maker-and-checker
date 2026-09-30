import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to studio-api (docker compose publishes it on 18200).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.STUDIO_API_URL ?? "http://localhost:18200",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
