import { useState } from "react";
import { LogIn } from "lucide-react";
import { useAuthStore } from "../store/useAuthStore";
import HyperliteLogo from "../components/HyperliteLogo";

export default function LoginScreen() {
  const login = useAuthStore((s) => s.login);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-anthracite-900">
      <form onSubmit={onSubmit} className="card w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2">
          <HyperliteLogo size={32} className="rounded-md" />
          <span className="text-base font-semibold tracking-wide text-anthracite-100">HYPERLITE</span>
        </div>

        <label className="text-xs font-medium text-anthracite-300">Utilisateur</label>
        <input className="input mt-1 mb-3" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />

        <label className="text-xs font-medium text-anthracite-300">Mot de passe</label>
        <input type="password" className="input mt-1" value={password} onChange={(e) => setPassword(e.target.value)} />

        {error && <p className="mt-3 text-sm text-status-error">{error}</p>}

        <button type="submit" disabled={loading} className="btn-primary mt-5 w-full justify-center">
          <LogIn size={15} /> {loading ? "Connexion..." : "Se connecter"}
        </button>
      </form>
    </div>
  );
}
