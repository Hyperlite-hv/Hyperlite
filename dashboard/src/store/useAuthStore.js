import { create } from "zustand";
import { setAuthToken, setUnauthorizedHandler } from "../api/client";

const KEY_TOKEN = "hyperlite_token";
const KEY_USERNAME = "hyperlite_username";
const KEY_ROLE = "hyperlite_role";

function clearStoredSession() {
  localStorage.removeItem(KEY_TOKEN);
  localStorage.removeItem(KEY_USERNAME);
  localStorage.removeItem(KEY_ROLE);
}

// Shared between login() and loginWith2FA(): a module-level function rather than a
// store method, because a zustand store is often destructured
// (`const { login } = useAuthStore()`), so a `this.xxx()` inside an action would
// lose its binding.
function applySession(set, token, username, role, totpEnabled, mustChangePassword = false) {
  localStorage.setItem(KEY_TOKEN, token);
  localStorage.setItem(KEY_USERNAME, username);
  localStorage.setItem(KEY_ROLE, role);
  setAuthToken(token);
  set({ token, username, role, totpEnabled, mustChangePassword, status: "authenticated", error: null });
}

export const useAuthStore = create((set, get) => ({
  token: null,
  username: null,
  role: null,
  totpEnabled: false,
  authSource: "local", // "local" or "sso" (an SSO account's password is managed by the identity provider)
  // The password no longer meets the policy: the server refuses everything but changing it (App shows that screen).
  mustChangePassword: false,
  status: "checking", // "checking" | "authenticated" | "anonymous"
  error: null,

  // An SSO sign-in that still needs this account's second factor: NextLogin shows the code/key step for it.
  ssoPending: null, // { preAuthToken, methods, username }

  async restoreSession() {
    // SSO: /auth/sso/callback redirects the browser to "/?sso=1" and leaves a one-time, HttpOnly handoff
    // cookie; the session is then asked for here. The session token never travels in a URL (it used to, as
    // ?sso_token=, and stayed in the browser history and the access logs).
    const params = new URLSearchParams(window.location.search);
    if (params.get("sso") === "1") {
      window.history.replaceState({}, "", window.location.pathname);
      try {
        const res = await fetch("/auth/sso/exchange", { method: "POST", credentials: "same-origin" });
        const data = await res.json().catch(() => null);
        if (!res.ok || !data) throw new Error((data && data.detail) || "SSO sign-in failed");
        if (data.require_2fa) {
          set({ status: "anonymous", ssoPending: { preAuthToken: data.pre_auth_token, methods: data.methods || ["totp"], username: null } });
          return;
        }
        setAuthToken(data.access_token);
        applySession(set, data.access_token, data.username, data.role, false, !!data.password_change_required);
        set({ authSource: "sso" });
        get().refreshMe();
        return;
      } catch (e) {
        setAuthToken(null);
        set({ status: "anonymous", error: e.message || "SSO sign-in failed" });
        return;
      }
    }

    const token = localStorage.getItem(KEY_TOKEN);
    if (!token) {
      set({ status: "anonymous" });
      return;
    }
    setAuthToken(token);
    try {
      const res = await fetch("/auth/me", { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error("session expired");
      const me = await res.json();
      set({ token, username: me.username, role: me.role, totpEnabled: !!me.totp_enabled, authSource: me.auth_source || "local", mustChangePassword: !!me.password_change_required, status: "authenticated" });
    } catch {
      clearStoredSession();
      setAuthToken(null);
      set({ status: "anonymous" });
    }
  },

  // Called by AccountSecurityModal after enabling/disabling 2FA, so the rest of the
  // UI (status badge) sees the up-to-date state without having to sign in again.
  async refreshMe() {
    if (!get().token) return;
    const res = await fetch("/auth/me", { headers: { Authorization: `Bearer ${get().token}` } });
    if (!res.ok) return;
    const me = await res.json();
    set({ totpEnabled: !!me.totp_enabled, authSource: me.auth_source || "local" });
  },

  // After "change my password": the server signed out every other session and returned a fresh token for
  // this one, which replaces the previous (now revoked) token everywhere.
  replaceToken(token) {
    localStorage.setItem(KEY_TOKEN, token);
    setAuthToken(token);
    set({ token, mustChangePassword: false });
  },

  // remember: "Stay signed in" asks the server for a longer session (see app/core/security.py).
  async login(username, password, remember = false) {
    set({ error: null });
    const body = new URLSearchParams({ username, password });
    if (remember) body.set("remember", "true");
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (data && data.detail) || "Invalid credentials";
      set({ error: msg });
      throw new Error(msg);
    }
    // 2FA: the password is correct but a TOTP code is still required. No session is
    // opened right away, the intermediate token is returned to the caller
    // (LoginScreen), which shows the code entry step and then calls loginWith2FA.
    if (data.require_2fa) {
      return { require2FA: true, preAuthToken: data.pre_auth_token, methods: data.methods || ["totp"] };
    }
    applySession(set, data.access_token, username, data.role, false, !!data.password_change_required);
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
    try { data = await res.json(); } catch { /* no body */ }
    if (!res.ok) {
      const msg = (data && data.detail) || "Invalid code";
      set({ error: msg });
      throw new Error(msg);
    }
    applySession(set, data.access_token, data.username || username, data.role, true, !!data.password_change_required);
  },

  // Second step with a security key: the challenge for this sign-in, the browser's WebAuthn prompt, then the answer.
  async loginWithSecurityKey(preAuthToken, username, getAssertion) {
    set({ error: null });
    const post = async (url, payload) => {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      let data = null;
      try { data = await res.json(); } catch { /* no body */ }
      if (!res.ok) throw new Error((data && data.detail) || "Security key refused");
      return data;
    };
    try {
      const options = await post("/auth/login/webauthn/options", { pre_auth_token: preAuthToken });
      const credential = await getAssertion(options);
      const data = await post("/auth/login/webauthn", { pre_auth_token: preAuthToken, credential });
      applySession(set, data.access_token, data.username || username, data.role, true, !!data.password_change_required);
    } catch (err) {
      set({ error: err.message });
      throw err;
    }
  },

  // Signs the session out on the server too: a session token is valid on its own until it expires, so forgetting
  // it here only used to leave every other tab and window (and any copy) signed in.
  logout() {
    const token = get().token;
    if (token) {
      fetch("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` }, keepalive: true }).catch(() => {});
    }
    clearStoredSession();
    setAuthToken(null);
    set({ token: null, username: null, role: null, totpEnabled: false, authSource: "local", mustChangePassword: false, status: "anonymous", ssoPending: null });
  },
}));

export function selectIsAdmin(state) {
  return state.role === "admin";
}

setUnauthorizedHandler(() => {
  if (useAuthStore.getState().status !== "authenticated") return;
  clearStoredSession();
  setAuthToken(null);
  useAuthStore.setState({ token: null, username: null, role: null, status: "anonymous", error: "Your session has expired. Please sign in again." });
});

// Another tab or window signed out (or signed in as someone else): follow it at once rather than keep acting with
// a session that is gone.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== KEY_TOKEN) return;
    const state = useAuthStore.getState();
    if (e.newValue === state.token) return;
    if (!e.newValue) {
      setAuthToken(null);
      useAuthStore.setState({ token: null, username: null, role: null, status: "anonymous", error: null });
    } else {
      useAuthStore.getState().restoreSession();
    }
  });
}
