import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

// Swagger UI for /docs (app/routers/api_docs.py), served by Hyperlite itself: an appliance often has no Internet
// access, so the page must not load it from a CDN.
function swaggerAssets() {
  return {
    name: "hyperlite-swagger-assets",
    apply: "build",
    closeBundle() {
      const dir = dirname(createRequire(import.meta.url).resolve("swagger-ui-dist/package.json"));
      mkdirSync("dist/swagger", { recursive: true });
      for (const f of ["swagger-ui-bundle.js", "swagger-ui.css", "LICENSE", "NOTICE"]) copyFileSync(join(dir, f), join("dist/swagger", f));
    },
  };
}

// In dev, proxies the real Hyperlite API paths (auth/vms/storage/...) to the
// FastAPI backend (port 8000), WITHOUT an /api prefix: once this dashboard is
// built and served by FastAPI itself (same origin), the frontend calls already
// point to the right paths with no extra configuration. websocket ("ws") enables
// the relay for the VNC console and the SSH terminal (real WS endpoints).
// The backend serves HTTPS (self-signed certificate, see data/tls/): "secure:
// false" disables trust-chain verification for this internal dev proxy only,
// without which Vite would reject the self-signed certificate.
const BACKEND = "http://127.0.0.1:8001";
const proxied = ["/auth", "/dashboard", "/vms", "/storage", "/networks", "/templates", "/isos", "/audit", "/health", "/host", "/nodes", "/tasks", "/containers", "/backups", "/jobs", "/pools", "/groups", "/acl", "/ha", "/notifications", "/update", "/vm-disks", "/vm-exports", "/api-docs", "/docs", "/openapi.json", "/swagger"];

export default defineConfig({
  plugins: [react(), swaggerAssets()],
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
  test: { exclude: ["e2e/**", "node_modules/**", "dist/**"] },
  server: {
    proxy: Object.fromEntries(
      proxied.map((p) => [p, { target: BACKEND, changeOrigin: true, ws: true, secure: false }])
    ),
  },
});
