import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxies the real Hyperlite API paths (auth/vms/storage/...) to the
// FastAPI backend (port 8000), WITHOUT an /api prefix: once this dashboard is
// built and served by FastAPI itself (same origin), the frontend calls already
// point to the right paths with no extra configuration. websocket ("ws") enables
// the relay for the VNC console and the SSH terminal (real WS endpoints).
// The backend serves HTTPS (self-signed certificate, see data/tls/): "secure:
// false" disables trust-chain verification for this internal dev proxy only,
// without which Vite would reject the self-signed certificate.
const BACKEND = "https://127.0.0.1:8000";
const proxied = ["/auth", "/dashboard", "/vms", "/storage", "/networks", "/templates", "/isos", "/audit", "/health"];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      proxied.map((p) => [p, { target: BACKEND, changeOrigin: true, ws: true, secure: false }])
    ),
  },
});
