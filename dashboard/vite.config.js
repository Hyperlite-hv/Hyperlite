import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En dev, proxy les vrais chemins de l'API Hyperlite (auth/vms/storage/...)
// vers le backend FastAPI (port 8000), SANS prefixe /api : une fois ce dashboard
// buildé et servi par FastAPI lui-même (meme origine), les appels front pointent
// deja vers les bons chemins sans configuration supplementaire. websocket ("ws")
// active le relais pour la console VNC et le terminal SSH (endpoints WS reels).
// Le backend sert desormais en HTTPS (certificat auto-signe, voir data/tls/) --
// "secure: false" desactive la verification de la chaine de confiance pour ce
// proxy interne uniquement, sans quoi Vite rejetterait le certificat auto-signe.
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
