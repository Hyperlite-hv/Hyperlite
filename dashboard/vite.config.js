import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En dev, le proxy /api pointe vers le vrai backend FastAPI de Hyperlite (port 8000).
// api/client.js bascule entre mockData.js et ces appels reels via USE_MOCK (voir ce fichier).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
