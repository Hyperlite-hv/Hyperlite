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

export const useAuthStore = create((set) => ({
  token: null,
  username: null,
  role: null,
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
      set({ token, username: me.username, role: me.role, status: "authenticated" });
    } catch (e) {
      clearStoredSession();
      setAuthToken(null);
      set({ status: "anonymous" });
    }
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
    localStorage.setItem(KEY_TOKEN, data.access_token);
    localStorage.setItem(KEY_USERNAME, username);
    localStorage.setItem(KEY_ROLE, data.role);
    setAuthToken(data.access_token);
    set({ token: data.access_token, username, role: data.role, status: "authenticated" });
  },

  logout() {
    clearStoredSession();
    setAuthToken(null);
    set({ token: null, username: null, role: null, status: "anonymous" });
  },
}));

export function selectIsAdmin(state) {
  return state.role === "admin";
}
