import { create } from "zustand";
import { setAuthToken } from "../api/client";

// Memes cles localStorage que l'ancien front vanilla-JS (app/static/app.js) --
// une fois ce dashboard servi depuis la meme origine que le backend, une
// session ouverte dans l'un est reconnue par l'autre.
const KEY_TOKEN = "hyperlite_token";
const KEY_USERNAME = "hyperlite_username";
const KEY_ROLE = "hyperlite_role";

function clearStoredSession() {
  localStorage.removeItem(KEY_TOKEN);
  localStorage.removeItem(KEY_USERNAME);
  localStorage.removeItem(KEY_ROLE);
}

// Factorise entre login() et loginWith2FA() (chantier 30) -- fonction de
// module plutot que methode du store : un store zustand est souvent
// destructure (`const { login } = useAuthStore()`), un `this.xxx()` a
// l'interieur d'une action perdrait alors sa liaison.
function applySession(set, token, username, role, totpEnabled) {
  localStorage.setItem(KEY_TOKEN, token);
  localStorage.setItem(KEY_USERNAME, username);
  localStorage.setItem(KEY_ROLE, role);
  setAuthToken(token);
  set({ token, username, role, totpEnabled, status: "authenticated" });
}

export const useAuthStore = create((set, get) => ({
  token: null,
  username: null,
  role: null,
  totpEnabled: false,
  status: "checking", // "checking" | "authenticated" | "anonymous"
  error: null,

  async restoreSession() {
    const token = localStorage.getItem(KEY_TOKEN);
    if (!token) {
      set({ status: "anonymous" });
      return;
    }
    setAuthToken(token);
    try {
      const res = await fetch("/auth/me", { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error("session expiree");
      const me = await res.json();
      set({ token, username: me.username, role: me.role, totpEnabled: !!me.totp_enabled, status: "authenticated" });
    } catch (e) {
      clearStoredSession();
      setAuthToken(null);
      set({ status: "anonymous" });
    }
  },

  // Rappelee par AccountSecurityModal (chantier 30) apres activation/
  // desactivation du 2FA, pour que le reste de l'UI (badge de statut) voie
  // l'etat a jour sans devoir se reconnecter.
  async refreshMe() {
    if (!get().token) return;
    const res = await fetch("/auth/me", { headers: { Authorization: `Bearer ${get().token}` } });
    if (!res.ok) return;
    const me = await res.json();
    set({ totpEnabled: !!me.totp_enabled });
  },

  async login(username, password) {
    set({ error: null });
    const body = new URLSearchParams({ username, password });
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* pas de corps */ }
    if (!res.ok) {
      const msg = (data && data.detail) || "Identifiants invalides";
      set({ error: msg });
      throw new Error(msg);
    }
    // Chantier 30 (2FA, 2026-09-17) : mot de passe correct mais un code
    // TOTP est encore requis -- pas de session ouverte tout de suite, on
    // renvoie le jeton intermediaire a l'appelant (LoginScreen) qui affiche
    // l'etape de saisie du code puis appelle loginWith2FA.
    if (data.require_2fa) {
      return { require2FA: true, preAuthToken: data.pre_auth_token };
    }
    applySession(set, data.access_token, username, data.role, false);
    return { require2FA: false };
  },

  async loginWith2FA(preAuthToken, code, username) {
    set({ error: null });
    const res = await fetch("/auth/login/2fa", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pre_auth_token: preAuthToken, code }),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* pas de corps */ }
    if (!res.ok) {
      const msg = (data && data.detail) || "Code invalide";
      set({ error: msg });
      throw new Error(msg);
    }
    applySession(set, data.access_token, username, data.role, true);
  },

  logout() {
    clearStoredSession();
    setAuthToken(null);
    set({ token: null, username: null, role: null, totpEnabled: false, status: "anonymous" });
  },
}));

export function selectIsAdmin(state) {
  return state.role === "admin";
}
